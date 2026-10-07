// Native textarea hit testing. The mirror exists only while measuring, never
// receives focus, and contains text nodes rather than interpreted markup.
const TEXT_STYLES = [
  'fontFamily', 'fontSize', 'fontStyle', 'fontWeight', 'fontStretch', 'fontVariant',
  'fontKerning', 'fontFeatureSettings', 'fontVariationSettings', 'fontOpticalSizing',
  'lineHeight', 'letterSpacing', 'wordSpacing', 'textAlign', 'textIndent',
  'textTransform', 'textRendering', 'direction', 'writingMode', 'textOrientation',
  'whiteSpace', 'overflowWrap', 'wordBreak', 'hyphens', 'tabSize',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
];
const px = value => Number.parseFloat(value) || 0;

function layoutSize(style, axis, fallback) {
  const size = Number.parseFloat(style[axis]);
  if (!Number.isFinite(size)) return fallback;
  if (style.boxSizing === 'border-box') return size;
  return size + (axis === 'width'
    ? px(style.paddingLeft) + px(style.paddingRight) + px(style.borderLeftWidth) + px(style.borderRightWidth)
    : px(style.paddingTop) + px(style.paddingBottom) + px(style.borderTopWidth) + px(style.borderBottomWidth));
}

function validRanges(value, ranges) {
  return (Array.isArray(ranges) ? ranges : []).filter(item =>
    item && item.entry && Number.isInteger(item.start) && Number.isInteger(item.end) &&
    item.start >= 0 && item.end > item.start && item.end <= value.length &&
    !/[\r\n]/.test(value.slice(item.start, item.end)) &&
    (typeof item.entry.alias !== 'string' || value.slice(item.start, item.end) === item.entry.alias));
}

function contains(rect, x, y) {
  return x >= rect.left && x < rect.right && y >= rect.top && y < rect.bottom;
}

/**
 * Return the original registered {start, end, entry} beneath a viewport point.
 * Offsets are UTF-16, matching codec.ranges(textarea.value) and DOM Range.
 * Ordinary prose, padding, clipped text and the blank part of a line return null.
 * No editing, selection or undo state is changed by this function.
 */
export function hitPromptReference(textarea, ranges, clientX, clientY) {
  if (textarea?.tagName !== 'TEXTAREA' || !textarea.isConnected ||
      !Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
  const sourceRect = textarea.getBoundingClientRect();
  if (!sourceRect.width || !sourceRect.height || !contains(sourceRect, clientX, clientY)) return null;
  const value = textarea.value, candidates = validRanges(value, ranges);
  if (!candidates.length) return null;
  const document = textarea.ownerDocument;
  const style = document.defaultView.getComputedStyle(textarea);
  const width = layoutSize(style, 'width', textarea.offsetWidth);
  const height = layoutSize(style, 'height', textarea.offsetHeight);
  if (!width || !height) return null;
  const scaleX = sourceRect.width / width, scaleY = sourceRect.height / height;
  const borderX = px(style.borderLeftWidth) + px(style.borderRightWidth);
  const borderY = px(style.borderTopWidth) + px(style.borderBottomWidth);
  // clientWidth excludes native scrollbars. Recover their integer width without
  // rounding the CSS content width, which may contain fractional pixels.
  const scrollbarX = Math.max(0, textarea.offsetWidth - textarea.clientWidth - Math.round(borderX));
  const scrollbarY = Math.max(0, textarea.offsetHeight - textarea.clientHeight - Math.round(borderY));
  const viewportWidth = width - borderX - scrollbarX;
  const viewportHeight = height - borderY - scrollbarY;
  const leftScrollbar = Math.max(0, textarea.clientLeft - Math.round(px(style.borderLeftWidth)));
  const viewport = {
    left: sourceRect.left + (px(style.borderLeftWidth) + leftScrollbar) * scaleX,
    top: sourceRect.top + px(style.borderTopWidth) * scaleY,
  };
  viewport.right = viewport.left + viewportWidth * scaleX;
  viewport.bottom = viewport.top + viewportHeight * scaleY;
  if (!contains(viewport, clientX, clientY)) return null;

  const mirror = document.createElement('div');
  mirror.setAttribute('aria-hidden', 'true');
  for (const property of TEXT_STYLES) mirror.style[property] = style[property];
  Object.assign(mirror.style, {
    position: 'fixed', left: '0', top: '0', width: `${viewportWidth}px`,
    height: `${viewportHeight}px`, boxSizing: 'border-box', border: '0', margin: '0',
    minWidth: '0', maxWidth: 'none', minHeight: '0', maxHeight: 'none',
    overflow: 'visible', visibility: 'hidden', pointerEvents: 'none',
  });
  const text = document.createTextNode(value);
  mirror.append(text);
  const host = document.body || document.documentElement;
  const range = document.createRange();
  try {
    host.append(mirror);
    const origin = mirror.getBoundingClientRect();
    const x = (clientX - viewport.left) / scaleX + textarea.scrollLeft + origin.left;
    const y = (clientY - viewport.top) / scaleY + textarea.scrollTop + origin.top;
    for (const candidate of candidates) {
      range.setStart(text, candidate.start);
      range.setEnd(text, candidate.end);
      for (const rect of range.getClientRects()) {
        if (rect.width > 0 && rect.height > 0 && contains(rect, x, y)) return candidate;
      }
    }
    return null;
  } finally {
    range.detach();
    mirror.remove();
  }
}

/**
 * Bind a single native click to onHit(entry, range, event).
 * getRanges(value) should be the live codec.ranges(value). This binding does not
 * prevent native input events or change value/selection; destroy() removes it.
 */
export function createPromptReferenceHit({textarea, getRanges, onHit} = {}) {
  if (textarea?.tagName !== 'TEXTAREA' || typeof getRanges !== 'function' || typeof onHit !== 'function') {
    throw new TypeError('createPromptReferenceHit needs a textarea, getRanges and onHit');
  }
  let composing = false, pointer = null;
  const compositionStart = () => { composing = true; pointer = null; };
  const compositionEnd = () => { composing = false; };
  const pointerDown = event => {
    pointer = event.button === 0 && event.isPrimary !== false && !composing
      ? {id: event.pointerId, x: event.clientX, y: event.clientY, dragged: false} : null;
  };
  const pointerMove = event => {
    if (pointer && pointer.id === event.pointerId &&
        Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) > 4) pointer.dragged = true;
  };
  const pointerCancel = () => { pointer = null; };
  const hitTest = (clientX, clientY) => hitPromptReference(
    textarea, getRanges(textarea.value), clientX, clientY);
  const click = event => {
    const gesture = pointer;
    pointer = null;
    if (composing || event.isComposing || event.button !== 0 || event.detail !== 1 ||
        event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || gesture?.dragged ||
        textarea.selectionStart !== textarea.selectionEnd) return;
    const found = hitTest(event.clientX, event.clientY);
    if (found) onHit(found.entry, found, event);
  };
  const listeners = [
    ['compositionstart', compositionStart], ['compositionend', compositionEnd],
    ['pointerdown', pointerDown], ['pointermove', pointerMove], ['pointercancel', pointerCancel], ['click', click],
  ];
  for (const [type, listener] of listeners) textarea.addEventListener(type, listener);
  return {
    hitTest,
    destroy() {
      for (const [type, listener] of listeners) textarea.removeEventListener(type, listener);
      pointer = null;
    },
  };
}
