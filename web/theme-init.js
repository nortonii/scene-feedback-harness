/* Run before styles load so a saved dark appearance never flashes white. */
(() => {
  let preference = null;
  try { preference = localStorage.getItem('astra-appearance-theme'); } catch { /* Private storage may be unavailable. */ }
  const theme = preference === 'dark' || preference === 'light'
    ? preference
    : (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#191b1e' : '#f5f4ef');
})();
