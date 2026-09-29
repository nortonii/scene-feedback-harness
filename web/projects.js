// Stored scene URLs remain relative to their own workspace. Scope them only
// when loading, so drafts and immutable feedback keep the original URLs.
export function projectPrefix(pathname) {
  const match = /^\/p\/([0-9a-f]{32})(?:\/|$)/i.exec(pathname || '');
  return match ? '/p/' + match[1] : '';
}

export function scopedURL(url, prefix) {
  if (typeof url !== 'string' || !prefix || !url.startsWith('/') || url.startsWith('//') || /^\/p\//.test(url)) return url;
  return prefix + url;
}

export function projectNavigationURL(project, origin) {
  if (!project || typeof project.project_id !== 'string' || !/^[0-9a-f]{32}$/i.test(project.project_id) || typeof project.url !== 'string') return null;
  try {
    const target = new URL(project.url, origin);
    if (target.origin !== origin || target.pathname !== '/p/' + project.project_id + '/') return null;
    return target.pathname + target.search + target.hash;
  } catch { return null; }
}
