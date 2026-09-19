(() => {
  const button = document.querySelector('.theme-toggle');
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    button?.setAttribute('aria-pressed', String(theme === 'dark'));
  }
  try { apply(localStorage.getItem('dimaggi-theme') === 'dark' ? 'dark' : 'light'); } catch { apply('light'); }
  button?.addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    apply(theme);
    try { localStorage.setItem('dimaggi-theme', theme); } catch {}
  });
})();
