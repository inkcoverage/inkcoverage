// Native disclosure navigation also works without JavaScript.
(() => {
  const menu = document.querySelector('.mobile-site-menu');
  if (!menu) return;
  const trigger = menu.querySelector('summary');
  const mobile = matchMedia('(max-width: 900px)');
  function close(restoreFocus = false) {
    menu.open = false;
    if (restoreFocus) trigger.focus({preventScroll: true});
  }
  for (const link of menu.querySelectorAll('a')) {
    if (new URL(link.href).pathname === location.pathname) link.setAttribute('aria-current', 'page');
  }
  document.addEventListener('click', event => {
    if (!menu.contains(event.target) || event.target.closest('a')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      event.preventDefault(); close(true);
    }
  });
  mobile.addEventListener('change', () => { if (!mobile.matches) close(); });
})();
