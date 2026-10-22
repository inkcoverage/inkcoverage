/* Optional GA4. No Google requests or event backlog before affirmative consent. */
(() => {
  'use strict';
  if (window.siteAnalytics) return;
  const site = document.currentScript?.dataset.site;
  const id = site === 'labelestimate' ? 'G-CXH8S10TXP' : site === 'inkcoverage' ? 'G-2FNRM9GVXD' : null;
  const production = location.hostname === site + '.app';
  const sample = site === 'labelestimate' && new URLSearchParams(location.search).has('example');
  const key = site + '_analytics_consent_v1';
  const visitKey = site + '_analytics_visit_v1';
  const ttl = 180 * 86400000;
  let choice = null, started = false, lastPage = null, previousFocus = null;
  let visit = { at: 0, events: [] };
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (saved && ['granted', 'denied'].includes(saved.value) && Date.now() - saved.at < ttl) choice = saved.value;
    visit = JSON.parse(sessionStorage.getItem(visitKey)) || visit;
  } catch { /* Private mode and full storage must not break the tools. */ }
  const copy = {
    en: ['Optional analytics', 'May we use Google Analytics to understand visits and tool use? No artwork, filenames, customer details or calculated values are sent. The tools work with either choice.', 'Allow analytics', 'No thanks', 'Analytics choices', 'Privacy'],
    de: ['Optionale Statistik', 'Dürfen wir Google Analytics verwenden, um Besuche und die Nutzung der Werkzeuge zu verstehen? Druckdaten, Dateinamen, Kundendaten und berechnete Werte werden nicht gesendet. Die Werkzeuge funktionieren mit beiden Optionen.', 'Statistik erlauben', 'Nein danke', 'Statistik-Einstellungen', 'Datenschutz'],
    fr: ['Statistiques facultatives', 'Pouvons-nous utiliser Google Analytics pour comprendre les visites et l’utilisation des outils ? Aucun visuel, nom de fichier, renseignement client ou résultat calculé n’est envoyé. Les outils fonctionnent quel que soit votre choix.', 'Autoriser les statistiques', 'Non merci', 'Choix des statistiques', 'Confidentialité'],
    it: ['Statistiche facoltative', 'Possiamo usare Google Analytics per capire le visite e l’uso degli strumenti? Non inviamo grafica, nomi di file, dati dei clienti o valori calcolati. Gli strumenti funzionano con entrambe le scelte.', 'Consenti statistiche', 'No grazie', 'Scelte statistiche', 'Privacy'],
    es: ['Estadísticas opcionales', '¿Podemos usar Google Analytics para entender las visitas y el uso de las herramientas? No enviamos diseños, nombres de archivo, datos de clientes ni valores calculados. Las herramientas funcionan con cualquiera de las opciones.', 'Permitir estadísticas', 'No, gracias', 'Opciones de estadísticas', 'Privacidad'],
    pt: ['Estatísticas opcionais', 'Podemos usar o Google Analytics para entender as visitas e o uso das ferramentas? Não enviamos artes, nomes de arquivos, dados de clientes ou valores calculados. As ferramentas funcionam com qualquer escolha.', 'Permitir estatísticas', 'Não, obrigado', 'Opções de estatísticas', 'Privacidade'],
    pl: ['Opcjonalne statystyki', 'Czy możemy używać Google Analytics, aby lepiej rozumieć odwiedziny i korzystanie z narzędzi? Nie wysyłamy projektów, nazw plików, danych klientów ani obliczonych wartości. Narzędzia działają niezależnie od wyboru.', 'Zezwól na statystyki', 'Nie, dziękuję', 'Ustawienia statystyk', 'Prywatność'],
    uk: ['Необов’язкова аналітика', 'Чи можна використовувати Google Analytics, щоб розуміти відвідування й використання інструментів? Макети, назви файлів, дані клієнтів та обчислені значення не надсилаються. Інструменти працюють за будь-якого вибору.', 'Дозволити аналітику', 'Ні, дякую', 'Налаштування аналітики', 'Конфіденційність'],
    zh: ['可选统计', '我们可以使用 Google Analytics 了解访问和工具使用情况吗？不会发送图稿、文件名、客户信息或计算值。无论您选择哪项，工具均可正常使用。', '允许统计', '不，谢谢', '统计设置', '隐私']
  };
  const lang = () => (document.documentElement.lang || 'en').slice(0, 2);
  let sawWelcome = !['#estimate', '#estimate-prices'].includes(location.hash);
  function estimatorEntry() {
    if (site !== 'labelestimate') return;
    if (['#estimate', '#estimate-prices'].includes(location.hash)) once('estimator_open', { entry_type: sawWelcome ? 'welcome' : 'direct' });
    else sawWelcome = true;
  }
  window.addEventListener('hashchange', estimatorEntry);
  const words = () => copy[lang()] || copy.en;
  const consent = granted => ({ analytics_storage: granted ? 'granted' : 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });
  const allowed = site === 'labelestimate'
    ? ['page_view', 'estimator_open', 'estimate_started', 'generate_pdf', 'template_saved']
    : ['page_view', 'file_upload', 'analysis_requested', 'analysis_completed', 'analysis_failed', 'ink_limit_requested', 'ink_limit_completed', 'ink_limit_failed'];
  const enums = {
    press_mode: ['flexo', 'digital', 'hybrid'], entry_type: ['welcome', 'direct'],
    analysis_mode: ['single', 'batch'], request_kind: ['new', 'retry', 'resume'],
    result_status: ['complete', 'partial', 'failed', 'cancelled'],
    failure_reason: ['network', 'busy', 'quota', 'expired', 'processing', 'cancelled']
  };
  function pageLocation() { return location.origin + location.pathname; }
  function context() {
    return { page_location: pageLocation(), page_title: site === 'labelestimate' ? 'LabelEstimate' : 'InkCoverage',
      page_referrer: referrer(), ui_language: lang(), workspace_kind: 'user' };
  }
  function referrer() {
    try { return document.referrer ? new URL(document.referrer).origin + '/' : ''; } catch { return ''; }
  }
  function track(name, values = {}) {
    if (!production || sample || choice !== 'granted' || !started || !allowed.includes(name)) return false;
    const params = context();
    // Only fixed categorical metadata can cross this boundary. Never pass raw UI/API data.
    for (const [field, options] of Object.entries(enums)) if (options.includes(values[field])) params[field] = values[field];
    if (site === 'labelestimate') params.language = lang(); // Preserve existing registered UI-language dimension.
    window.gtag('event', name, params);
    return true;
  }
  function once(name, values) {
    if (!visit || !Array.isArray(visit.events) || Date.now() - visit.at > 1800000) visit = { at: Date.now(), events: [] };
    if (visit.events.includes(name)) return false;
    if (!track(name, values)) return false;
    visit.events.push(name); visit.at = Date.now();
    try { sessionStorage.setItem(visitKey, JSON.stringify(visit)); } catch { /* In-memory deduplication still works. */ }
    return true;
  }
  function page() {
    const path = pageLocation();
    if (path !== lastPage && track('page_view')) lastPage = path;
  }
  function start() {
    if (started || !production || sample || choice !== 'granted') return;
    started = true;
    window['ga-disable-' + id] = false;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag('consent', 'default', consent(false));
    window.gtag('consent', 'update', consent(true));
    window.gtag('js', new Date());
    window.gtag('config', id, { ...context(), send_page_view: false, allow_google_signals: false,
      allow_ad_personalization_signals: false, cookie_expires: ttl / 1000, cookie_update: false,
      ...(new URLSearchParams(location.search).get('analytics_debug') === '1' ? { debug_mode: true, traffic_type: 'internal' } : {}) });
    const script = document.createElement('script'); script.async = true;
    script.src = 'https://www.googletagmanager.com/gtag/js?id=' + id;
    document.head.append(script);
    page();
    estimatorEntry();
  }
  function clearCookies() {
    const domains = ['', location.hostname, '.' + location.hostname];
    for (const cookie of document.cookie.split(';')) {
      const name = cookie.trim().split('=')[0];
      if (/^_ga(?:_|$)|^_gid$|^_gat/.test(name)) for (const domain of domains) {
        document.cookie = name + '=; Max-Age=0; path=/;' + (domain ? ' domain=' + domain + ';' : '') + ' SameSite=Lax';
      }
    }
  }
  function choose(value) {
    choice = value;
    try { localStorage.setItem(key, JSON.stringify({ value, at: Date.now() })); } catch { /* Respect choice in memory. */ }
    if (value === 'granted') start();
    else {
      window['ga-disable-' + id] = true;
      if (started) window.gtag('consent', 'update', consent(false));
      clearCookies();
    }
    banner.hidden = true;
    previousFocus?.focus?.();
    // Reload after withdrawal/re-grant removes any previously loaded Google code and listeners.
    if (started && value === 'denied') location.reload();
  }
  const banner = document.createElement('section'); banner.id = 'analytics-choice'; banner.hidden = true;
  banner.setAttribute('aria-labelledby', 'analytics-title');
  const title = document.createElement('strong'); title.id = 'analytics-title';
  const description = document.createElement('p');
  const accept = document.createElement('button'), reject = document.createElement('button'), privacy = document.createElement('a');
  accept.type = reject.type = 'button'; accept.onclick = () => choose('granted'); reject.onclick = () => choose('denied');
  const actions = document.createElement('div'); actions.append(accept, reject, privacy); banner.append(title, description, actions);
  const settings = document.createElement('button'); settings.id = 'analytics-settings'; settings.type = 'button';
  function localize() {
    const w = words(); title.textContent = w[0]; description.textContent = w[1]; accept.textContent = w[2]; reject.textContent = w[3]; settings.textContent = w[4]; privacy.textContent = w[5];
    privacy.href = (lang() === 'en' ? '/' : '/' + lang() + '/') + 'privacy';
  }
  settings.onclick = () => { previousFocus = document.activeElement; localize(); banner.hidden = false; accept.focus(); };
  banner.addEventListener('keydown', event => { if (event.key === 'Escape' && choice) { banner.hidden = true; settings.focus(); } });
  window.siteAnalytics = { track, once, page };
  window.addEventListener('storage', event => {
    if (event.key === key && started) {
      let next = null;
      try { next = JSON.parse(event.newValue); } catch { /* Clearing consent withdraws it. */ }
      if (next?.value !== 'granted') {
        choice = 'denied'; window['ga-disable-' + id] = true; clearCookies(); location.reload();
      }
    }
  });
  function mount() {
    localize(); document.body.append(banner, settings);
    new MutationObserver(localize).observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    if (production && !sample && !choice) banner.hidden = false;
    start();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true }); else mount();
})();
