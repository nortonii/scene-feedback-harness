// Read-only evidence views: no new capture, no queue actions, no changes to payloads.
import { compactReferenceMessage, savedReferenceDisplayMetadata } from './prompt-reference-display.js';
function element(tag,text,className) {
  const node=document.createElement(tag);if(text!==undefined) node.textContent=text;if(className) node.className=className;return node;
}
export function savedEvidence(feedback) {
  const rows=[];
  const media=(item,key)=>item[key+'_url'] || item[key+'_data_url'];
  function sceneRow(item,name) {
    const variants=[['标注图','scene_annotated'],['原图','scene_original'],['叠图对比','scene_comparison']]
      .map(([label,key])=>({label,url:media(item,key)})).filter(item=>item.url);
    if(variants.length) rows.push({name,detail:`v${item.scene_revision ?? feedback.scene_revision ?? '—'}`,variants});
  }
  sceneRow(feedback,'提交时的场景');
  for(const shot of feedback.scene_snapshots || []) sceneRow(shot,shot.name || '保存的截图');
  for(const moment of feedback.dynamic_frames || []) {
    const name=`${moment.view_name || '视频时刻'} · ${Number(moment.time_sec || 0).toFixed(3)} 秒`;
    sceneRow(moment,name);
    const original=media(moment,'reference_original'),marked=media(moment,'reference_annotated');
    if(original||marked) rows.push({name:name+' · 参考帧',variants:[{label:'标注图',url:marked},{label:'原图',url:original}].filter(item=>item.url)});
  }
  for(const ref of feedback.reference_images || []) {
    const marked=(feedback.reference_annotated_images || []).find(item=>item.reference_id===ref.id)?.url || (feedback.reference_annotated_data_urls || []).find(item=>item.reference_id===ref.id)?.data_url;
    rows.push({name:ref.name || '参考图',variants:[{label:'标注图',url:marked},{label:'原图',url:ref.url}].filter(item=>item.url)});
  }
  for(const ref of feedback.image_refs || []) {
    const variants=[{label:'标注图',url:media(ref,'annotated')},{label:'原图',url:media(ref,'original')}].filter(item=>item.url);
    rows.push({name:ref.label || ref.name || '额外图片引用',variants});
  }
  for(const pose of [...feedback.human_pose || [],...feedback.human_pose_edits || [],...feedback.pose_refs || [],...feedback.pose_edits || []]) {
    const url=pose.pose_overlay_url || pose.overlay_data_url;
    rows.push({name:pose.label || '人体姿态证据',variants:url?[{label:'骨架',url}]:[],detail:url?'':'姿态数据'});
  }
  const shots=(feedback.scene_snapshots || []).length, moments=(feedback.dynamic_frames || []).length;
  return {summary:[shots?`${shots} 张截图`:null,moments?`${moments} 个时刻`:null,`${(feedback.annotations || []).length} 个标记`].filter(Boolean).join(' · '),rows};
}
export function setupFeedbackEvidence({getDraft,api,resourceURL}) {
  const byId=id=>document.getElementById(id);
  const details=byId('feedback-evidence'),summary=byId('feedback-evidence-summary'),list=byId('feedback-evidence-list');
  const dialog=byId('feedback-preview-dialog'),status=byId('feedback-preview-status'),media=byId('feedback-preview-media');
  const cache=new Map();let signature='',opener=null,request=0,currentId=null;
  function renderRows(parent,rows) {
    parent.replaceChildren();
    for(const row of rows) {
      const card=element('article',undefined,'evidence-card'),heading=element('strong',row.name);
      card.append(heading);
      if(row.detail) card.append(element('small',row.detail));
      const variants=row.variants || (row.url?[{label:'预览',url:row.url}]:[]);
      if(variants.length) {
        const img=element('img');img.alt=row.name;img.loading='lazy';img.src=resourceURL(variants[0].url);
        if(parent===media) {
          const zoom=element('button',undefined,'evidence-image');zoom.type='button';zoom.setAttribute('aria-label','放大查看 '+row.name);zoom.setAttribute('aria-expanded','false');zoom.append(img);card.append(zoom);
          zoom.addEventListener('click',()=> {const expanded=card.classList.toggle('is-expanded');zoom.setAttribute('aria-expanded',String(expanded));zoom.setAttribute('aria-label',(expanded?'缩小预览 ':'放大查看 ')+row.name);});
        } else card.append(img);
        img.addEventListener('error',()=> {img.hidden=true;card.append(element('small','预览暂不可用，原反馈仍保留。'));},{once:true});
        if(variants.length>1) {
          const actions=element('div',undefined,'evidence-variants');
          for(const variant of variants) {
            const button=element('button',variant.label,'compact-button');button.type='button';button.setAttribute('aria-pressed',String(variant===variants[0]));
            button.addEventListener('click',()=>{img.hidden=false;img.src=resourceURL(variant.url);for(const other of actions.children) other.setAttribute('aria-pressed',String(other===button));});actions.append(button);
          }
          card.append(actions);
        }
      }
      parent.append(card);
    }
  }
  function refresh() {
    const draft=getDraft();const next=JSON.stringify(draft,(key,value)=>key==='url' && typeof value==='string' && value.startsWith('data:') ? value.slice(0,24)+value.length : value);
    if(next===signature) return;signature=next;
    summary.textContent='本次反馈 · '+draft.summary;
    byId('feedback-evidence-description').textContent=draft.description;
    if(details.open) renderRows(list,draft.rows);
  }
  details.addEventListener('toggle',()=> {if(details.open) {signature='';refresh();}});
  function rememberSaved(result) {
    if(result?.feedback_id) {cache.set(result.feedback_id,result);if(cache.size>100) cache.delete(cache.keys().next().value);}
  }
  async function preview(id,trigger) {
    const generation=++request;currentId=id;opener=trigger || opener;
    if(!dialog.open) dialog.showModal();
    status.textContent='正在读取已保存的反馈…';byId('feedback-preview-note').textContent='';media.replaceChildren();byId('feedback-preview-retry').hidden=true;
    try {
      const feedback=cache.get(id) || await api('/api/workspace/feedback/'+encodeURIComponent(id));
      if(generation!==request || !dialog.open) return;
      rememberSaved(feedback);const evidence=savedEvidence(feedback);
      status.textContent=`已保存 · ${evidence.summary} · v${feedback.scene_revision}`;
      byId('feedback-preview-note').textContent=compactReferenceMessage(feedback.note,savedReferenceDisplayMetadata(feedback)) || '本轮未填写文字说明';
      renderRows(media,evidence.rows);
    } catch(error) {
      if(generation!==request || !dialog.open) return;
      status.textContent='读取失败：'+error.message;byId('feedback-preview-retry').hidden=false;
    }
  }
  function attachReceipt(card,id) {
    if(!/^[a-f0-9]{32}$/.test(id || '')) return;
    const saved=cache.get(id),button=element('button',saved?'反馈已保存 · '+savedEvidence(saved).summary+' · 查看附件':'反馈已保存 · 查看图片与标记','feedback-receipt');
    button.type='button';button.dataset.feedbackId=id;button.addEventListener('click',()=>preview(id,button));card.append(button);
  }
  byId('feedback-preview-close').addEventListener('click',()=>dialog.close());
  byId('feedback-preview-retry').addEventListener('click',()=>preview(currentId));
  dialog.addEventListener('close',()=> {request++;if(opener?.isConnected) opener.focus({preventScroll:true});});
  refresh();return {refresh,attachReceipt,rememberSaved};
}
