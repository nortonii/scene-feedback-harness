// Restore before the first paint, including when the renderer is still loading.
try {
  document.documentElement.dataset.layout = localStorage.getItem('astra-workspace-layout') === 'immersive' ? 'immersive' : 'compare';
} catch { document.documentElement.dataset.layout = 'compare'; }
document.documentElement.dataset.referenceVisible = 'false';

document.documentElement.dataset.toolsVisible = 'false';
