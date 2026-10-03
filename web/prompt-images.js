const IMAGE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const JPEG = /^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/;

export function imageToken(id) { return IMAGE_ID.test(id || '') ? `[[image:${id}]]` : null; }

export function validImageReference(item) {
  if (!item || !imageToken(item.id) || !['reference','scene'].includes(item.pane) ||
      typeof item.label !== 'string' || !item.label || item.label.length > 180 || /[\x00-\x1f\x7f]/.test(item.label) ||
      !JPEG.test(item.original_data_url || '') ||
      (item.annotated_data_url !== undefined && !JPEG.test(item.annotated_data_url)) ||
      !Number.isInteger(item.image_width) || item.image_width <= 0 ||
      !Number.isInteger(item.image_height) || item.image_height <= 0) return false;
  if (item.pane === 'reference' && typeof item.reference_id !== 'string') return false;
  if (item.pane === 'scene' && (!Number.isInteger(item.scene_revision) || !item.camera ||
      !['position','target','up'].every((key) => Array.isArray(item.camera[key]) &&
        item.camera[key].length === 3 && item.camera[key].every(Number.isFinite)))) return false;
  return item.time_sec === undefined || Number.isFinite(item.time_sec);
}

export function collectImageReferences(note, references) {
  const matches = [...note.matchAll(/\[\[image:([A-Za-z0-9_-]{1,64})\]\]/g)];
  const starts = new Set(matches.map((match) => match.index));
  for (const match of note.matchAll(/\[\[image:/g)) {
    if (!starts.has(match.index)) throw new Error('图片引用不完整，请用输入框的 ＋ 重新添加图片。');
  }
  const ids = [...new Set(matches.map((match) => match[1]))];
  if (ids.length > 8) throw new Error('一条提示最多引用 8 张图片。');
  return ids.map((id) => {
    const items = references.filter((item) => item?.id === id);
    if (items.length !== 1 || !validImageReference(items[0])) {
      throw new Error('提示引用的图片无法恢复，请移除这处引用，再用输入框的 ＋ 重新添加。');
    }
    return items[0];
  });
}

// Store the image bytes separately from the small localStorage text draft.
// Every save captures its collection before joining the serialized write queue.
export function createPromptImageStore(openDatabase) {
  let pending = Promise.resolve();
  return {
    get pending() { return pending; },
    save(sessionId, references) {
      const images = structuredClone(references);
      pending = pending.catch(() => {}).then(async () => {
        const db = await openDatabase();
        try {
          await new Promise((resolve, reject) => {
            const tx = db.transaction('drafts', 'readwrite');
            tx.objectStore('drafts').put(images, 'prompt-images:' + sessionId);
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error || new Error('图片草稿保存失败'));
          });
        } finally { db.close(); }
      });
      return pending;
    },
    async load(sessionId) {
      const db = await openDatabase();
      try {
        const images = await new Promise((resolve, reject) => {
          const request = db.transaction('drafts').objectStore('drafts').get('prompt-images:' + sessionId);
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
        return Array.isArray(images) ? images.filter(validImageReference).slice(0, 16) : [];
      } finally { db.close(); }
    }
  };
}
