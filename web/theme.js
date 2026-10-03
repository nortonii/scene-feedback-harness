const STORAGE_KEY = 'astra-appearance-theme';
const validTheme = value => value === 'dark' || value === 'light';

function storedTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return validTheme(value) ? value : null;
  } catch {
    return null;
  }
}

/** Keep app chrome and the renderer in sync without changing model materials. */
export function setupTheme({ onChange } = {}) {
  const root = document.documentElement;
  const button = document.getElementById('theme-toggle');
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let preference = storedTheme();
  let transitionTimer = 0;
  let theme = preference || (system.matches ? 'dark' : 'light');

  function apply(next, { animate = true } = {}) {
    const changed = root.dataset.theme !== next;
    clearTimeout(transitionTimer);
    root.classList.toggle('theme-transitioning', changed && animate && !reducedMotion.matches);
    theme = next;
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#191b1e' : '#f5f4ef');
    if (button) {
      const label = theme === 'dark' ? '切换到日间模式' : '切换到夜间模式';
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.setAttribute('aria-pressed', String(theme === 'dark'));
    }
    onChange?.({ dark: theme === 'dark', animate: changed && animate && !reducedMotion.matches });
    if (root.classList.contains('theme-transitioning')) {
      transitionTimer = window.setTimeout(() => root.classList.remove('theme-transitioning'), 480);
    }
  }

  function toggle() {
    preference = theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(STORAGE_KEY, preference); } catch { /* Still allow this tab to change theme. */ }
    apply(preference);
  }

  function systemChanged() {
    if (!preference) apply(system.matches ? 'dark' : 'light');
  }

  function storageChanged(event) {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    preference = storedTheme();
    apply(preference || (system.matches ? 'dark' : 'light'));
  }

  button?.addEventListener('click', toggle);
  system.addEventListener('change', systemChanged);
  window.addEventListener('storage', storageChanged);
  apply(theme, { animate: false });

  return {
    get dark() { return theme === 'dark'; },
    dispose() {
      clearTimeout(transitionTimer);
      root.classList.remove('theme-transitioning');
      button?.removeEventListener('click', toggle);
      system.removeEventListener('change', systemChanged);
      window.removeEventListener('storage', storageChanged);
    },
  };
}
