// Decoded images are shared between the foreground view and nearby-frame loads.
// Call prepare for a new center, then prefetch its neighbors. URLs are immutable.
export function createFrameImageCache({maxEntries=32, maxBytes=80 * 1024 * 1024,
  prefetchConcurrency=2}={}) {
  maxEntries = Math.max(1, Math.floor(Number(maxEntries) || 32));
  maxBytes = Math.max(1, Number(maxBytes) || 80 * 1024 * 1024);
  prefetchConcurrency = Math.max(0, Math.floor(Number(prefetchConcurrency) || 0));
  const entries = new Map();
  let bytes = 0, generation = 0;
  let foreground = new Set(), displayed = new Set(), neighbors = new Set();
  let queue = [], batchController = null;

  function abortError() {
    return new DOMException('Frame image request superseded', 'AbortError');
  }
  function urlsList(urls) {
    if (!Array.isArray(urls) || urls.some((url) => typeof url !== 'string' || !url)) {
      throw new TypeError('Frame image URLs must be an array of nonempty strings');
    }
    return urls;
  }
  function abortable(promise, signal) {
    return new Promise((resolve, reject) => {
      const aborted = () => { signal.removeEventListener('abort', aborted); reject(abortError()); };
      if (signal.aborted) aborted();
      else signal.addEventListener('abort', aborted, {once:true});
      // Both branches consume late decode/fetch outcomes after cancellation.
      promise.then((value) => {
        signal.removeEventListener('abort', aborted);
        if (signal.aborted) reject(abortError());
        else resolve(value);
      }, (error) => {
        signal.removeEventListener('abort', aborted);
        reject(signal.aborted ? abortError() : error);
      });
    });
  }
  function touch(entry) {
    if (!entry || entries.get(entry.url) !== entry) return;
    entries.delete(entry.url); entries.set(entry.url, entry);
  }
  function pinned(url) { return foreground.has(url) || displayed.has(url); }
  function remove(entry) {
    if (entries.get(entry.url) === entry) {
      entries.delete(entry.url); bytes -= entry.bytes; entry.bytes = 0;
    }
    entry.controller.abort();
    // A revoked, already decoded image can remain visible until the DOM replaces
    // it. Clearing its src here would blank that view during the next load.
    if (entry.status !== 'ready') entry.image.removeAttribute('src');
    if (entry.objectUrl) { URL.revokeObjectURL(entry.objectUrl); entry.objectUrl = null; }
  }
  function trim() {
    for (const entry of entries.values()) {
      if (entries.size <= maxEntries && bytes <= maxBytes) break;
      if (!pinned(entry.url)) remove(entry);
    }
    // A foreground group may exceed the budget; it must remain displayable.
  }
  function alive(entry) {
    if (entry.controller.signal.aborted || entries.get(entry.url) !== entry) throw abortError();
  }
  function backgroundCount() {
    let count = 0;
    for (const entry of entries.values()) {
      if (entry.status === 'pending' && !foreground.has(entry.url)) count++;
    }
    return count;
  }
  function pump() {
    while (queue.length && backgroundCount() < prefetchConcurrency) {
      const url = queue.shift();
      if (!neighbors.has(url) || foreground.has(url) || entries.has(url)) continue;
      start(url, 'low');
    }
  }
  function start(url, priority) {
    const existing = entries.get(url);
    if (existing) { touch(existing); return existing; }
    const image = new Image();
    image.decoding = 'async'; image.fetchPriority = priority;
    image.dataset.sourceUrl = url;
    const entry = {url, image, controller:new AbortController(), status:'pending',
      objectUrl:null, bytes:0, promise:null};
    entries.set(url, entry);
    entry.promise = (async () => {
      try {
        const response = await fetch(url, {signal:entry.controller.signal, priority});
        if (!response.ok) throw new Error('Frame image failed to load: HTTP ' + response.status);
        const blob = await response.blob();
        alive(entry);
        entry.bytes = blob.size; bytes += entry.bytes;
        trim(); alive(entry);
        entry.objectUrl = URL.createObjectURL(blob);
        image.src = entry.objectUrl;
        if (image.decode) await abortable(image.decode(), entry.controller.signal);
        else await abortable(new Promise((resolve, reject) => {
          image.onload = resolve;
          image.onerror = () => reject(new Error('Frame image failed to decode'));
          if (image.complete) image.naturalWidth ? resolve() : reject(new Error('Frame image failed to decode'));
        }), entry.controller.signal);
        alive(entry);
        if (!image.naturalWidth || !image.naturalHeight) throw new Error('Frame image has no pixels');
        const decodedBytes = image.naturalWidth * image.naturalHeight * 4;
        entry.bytes += decodedBytes; bytes += decodedBytes; entry.status = 'ready';
        trim();
        return image;
      } catch (error) {
        remove(entry);
        throw error;
      }
    })();
    // Failed/aborted background work is silent. The original promise still
    // rejects for a foreground caller, and failed entries can be retried.
    entry.promise.then(pump, pump).catch(() => {});
    trim();
    return entry;
  }

  function prepare(urls) {
    urls = urlsList(urls);
    const current = ++generation;
    batchController?.abort();
    const controller = new AbortController(); batchController = controller;
    foreground = new Set(urls);
    neighbors = new Set(); queue = [];
    for (const entry of entries.values()) {
      if (entry.status === 'pending' && !foreground.has(entry.url)) remove(entry);
    }
    const requests = urls.map((url) => start(url, 'high').promise);
    const result = abortable(Promise.all(requests), controller.signal).then((images) => {
      if (current !== generation) throw abortError();
      displayed = new Set(urls);
      for (const url of urls) touch(entries.get(url));
      trim();
      return images;
    });
    // A superseded caller may have already stopped awaiting its batch.
    result.catch(() => {});
    return result;
  }
  function prefetch(urls) {
    urls = urlsList(urls);
    neighbors = new Set(urls.filter((url) => !foreground.has(url)));
    for (const entry of entries.values()) {
      if (entry.status === 'pending' && !foreground.has(entry.url) && !neighbors.has(entry.url)) remove(entry);
    }
    queue = [...neighbors].filter((url) => !entries.has(url));
    pump();
  }
  function retain(urls) {
    urls = urlsList(urls);
    const wanted = new Set(urls);
    if (wanted.size === foreground.size && [...wanted].every((url) => foreground.has(url))) return;
    // Returning to an already visible/cached view still supersedes a pending
    // camera switch, without downloading that visible image again.
    generation++; batchController?.abort(); batchController = null;
    foreground = wanted; displayed = new Set(urls);
    neighbors = new Set(); queue = [];
    for (const entry of entries.values()) {
      if (entry.status === 'pending' && !wanted.has(entry.url)) remove(entry);
    }
    trim();
  }
  function clear() {
    generation++; batchController?.abort(); batchController = null;
    foreground = new Set(); displayed = new Set(); neighbors = new Set(); queue = [];
    for (const entry of entries.values()) remove(entry);
    bytes = 0;
  }
  return {prepare, prefetch, retain, clear};
}
