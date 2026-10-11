const STORAGE_KEY='astra-render-depth-limit';
const MIN_DEPTH=.1,MAX_DEPTH=100000;
const valid=value=>typeof value==='number' && Number.isFinite(value) && value>=MIN_DEPTH && value<=MAX_DEPTH;
const label=value=>String(Number(value.toPrecision(8)));

export function setupRenderSettings({onChange}) {
  const input=document.getElementById('render-depth-input'),range=document.getElementById('render-depth-range');
  const auto=document.getElementById('render-depth-auto'),status=document.getElementById('render-depth-status');
  let limit=null,current=2000,error=false;
  try { const saved=JSON.parse(localStorage.getItem(STORAGE_KEY));if(valid(saved))limit=saved; }
  catch { /* An unavailable or broken preference uses automatic scene depth. */ }
  function refresh(far=current) {
    if(Number.isFinite(far) && far>0)current=far;
    auto.setAttribute('aria-pressed',String(limit===null));
    if(document.activeElement!==input && !error)input.value=label(limit ?? current);
    range.value=String(Math.round((Math.log10(Math.max(MIN_DEPTH,Math.min(MAX_DEPTH,limit ?? current)))+1)*100));
    range.setAttribute('aria-valuetext',label(limit ?? current)+' 场景单位');
    if(!error)status.textContent=limit===null ? '自动 · 当前 '+label(current) : '手动 · '+label(limit)+' 场景单位';
  }
  function setLimit(value) {
    limit=value;error=false;input.removeAttribute('aria-invalid');
    try { if(limit===null)localStorage.removeItem(STORAGE_KEY);else localStorage.setItem(STORAGE_KEY,JSON.stringify(limit)); }
    catch { /* Keep this tab usable when browser storage is unavailable. */ }
    onChange();refresh();
  }
  function readInput(commit=false) {
    if(input.value==='' && !input.validity.badInput) { if(commit)setLimit(null);return; }
    const value=input.valueAsNumber;
    if(valid(value))setLimit(value);
    else if(commit) {
      error=true;input.setAttribute('aria-invalid','true');
      status.textContent='请输入 0.1–100000 之间的距离。';
    }
  }
  input.addEventListener('input',()=>readInput());
  input.addEventListener('change',()=>readInput(true));
  range.addEventListener('input',()=>setLimit(Number((10**(Number(range.value)/100-1)).toPrecision(8))));
  auto.addEventListener('click',()=>setLimit(null));
  refresh();
  return {refresh,get limit(){return limit;}};
}
