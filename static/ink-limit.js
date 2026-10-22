// All-language feature; average results keep their own scope and readings.
(() => {
  const button = $('#btnCheckInkLimit'), toggle = $('#btnToggleHeatmap'), overlayToggle = $('#btnToggleOverlay'), overlay = $('#inkLimitOverlay');
  const panel = $('#inkLimitResults'), dialog = $('#inkLimitDialog'), form = $('#inkLimitForm');
  const list = $('#inkLimitChannelList'), value = $('#inkLimitValue'), start = $('#btnRunInkLimit');
  const dialogError = $('#inkLimitDialogError'), status = $('#inkLimitChannelStatus'), selection = $('#inkLimitSelection');
  const results = new Map(), channelCache = new Map();
  let scope = null, active = null, channelLoading = false, view = 'overlay', error = null, rendered = null;
  let renderedLang = null;
  let documentVersion = state.documentVersion;
  const ready = () => state.pdfId && state.previewReady && !state.isLoading && !state.isAnalyzing
    && !state.sourceUnavailable && !sourceTimeExpired() && !isDragging && !isResizing && !window.channelPreview?.busy();
  const current = () => {
    const entry = results.get(state.currentPage);
    return entry && sameMeasurement(entry.scope) ? entry : null;
  };
  const chosen = () => [...list.querySelectorAll('input:checked')].map(input => input.value);
  const setError = message => { dialogError.textContent = message; dialogError.hidden = !message; };
  const close = () => { if (dialog.open) dialog.close(); };
  function validate() {
    const missing = !channelLoading && !chosen().length;
    start.disabled = channelLoading || !ready() || missing || !value.checkValidity();
    if (missing && dialogError.hidden) setError(t('ink_required'));
    if (state.sourceUnavailable || sourceTimeExpired()) setError(t('file_expired_short'));
  }
  function validChannels(items) {
    return Array.isArray(items) && items.length > 0 && items.length <= 64
      && items.every(ch => typeof ch?.name === 'string' && ch.name.length > 0 && ch.name.length <= 1024)
      && new Set(items.map(ch => ch.name)).size === items.length;
  }
  function channels(items) {
    const process = {Cyan: 0, Magenta: 1, Yellow: 2, Black: 3};
    const averaged = validResult(state.currentPage)?.channels || [];
    list.replaceChildren();
    for (const ch of [...items].sort((a, b) => (process[a.name] ?? 4) - (process[b.name] ?? 4))) {
      const label = resultElement('label', 'ink-limit-channel');
      const input = document.createElement('input'); input.type = 'checkbox'; input.value = ch.name; input.checked = true;
      const swatch = resultElement('span', 'ink-swatch'); swatch.setAttribute('aria-hidden', 'true');
      const color = ch.displayColor || averaged.find(ink => ink.name === ch.name)?.displayColor;
      if (!isSpotColor(ch.name)) swatch.style.background = getInkColor(ch.name);
      else {
        swatch.classList.add('spot-swatch');
        if (/^#[0-9a-f]{6}$/i.test(color || '')) { swatch.classList.add('has-color'); swatch.style.background = color; }
      }
      label.append(input, swatch, document.createTextNode(ch.name)); list.append(label);
    }
    status.textContent = ''; channelLoading = false; validate();
  }
  async function post(path, body, fallback) {
    try {
      return await apiResult(await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}), fallback);
    } finally { refreshAllowance(); }
  }
  async function open() {
    if (!ready() || dialog.open) return;
    scope = captureScope(); const opening = scope;
    form.elements.inkLimitArea.value = 'full';
    selection.disabled = !state.crop || state.crop.w <= .01 || state.crop.h <= .01;
    setError(''); list.replaceChildren();
    button.setAttribute('aria-expanded', 'true'); dialog.showModal();
    form.querySelector('input[value="full"]').focus();
    const info = state.pageColors[state.currentPage], cached = channelCache.get(state.currentPage);
    if (cached) { channels(cached); return; }
    if (info?.inkChannelsStatus === 'complete' && validChannels(info.inkChannels)) {
      channelCache.set(state.currentPage, info.inkChannels); channels(info.inkChannels); return;
    }
    channelLoading = true; status.textContent = t('loading_inks'); start.disabled = true;
    const request = beginAnalysis('ink-limit-channels');
    if (!request) { channelLoading = false; setError(t('document_busy')); validate(); return; }
    active = request; sync();
    try {
      const data = await post('/api/ink-limit/channels', {id: request.id, page: request.page}, t('inks_load_failed'));
      if (!analysisIsCurrent(request) || scope !== opening) return;
      if (!validChannels(data.channels)) throw new Error(t('inks_read_failed'));
      channelCache.set(request.page, data.channels); channels(data.channels);
    } catch (err) {
      if (!analysisIsCurrent(request) || scope !== opening) return;
      if ([404, 410].includes(err.status)) state.sourceUnavailable = true;
      channelLoading = false; status.textContent = ''; setError(err.message);
    } finally {
      if (active === request) active = null;
      finishAnalysis(request); sync();
    }
  }
  function validData(data, request, inks, limit) {
    return data.page === request.page && data.limit === limit
      && data.widthPt === state.pdfWidthPt && data.heightPt === state.pdfHeightPt
      && Number.isInteger(data.dpi) && data.dpi > 0 && data.dpi <= 600
      && data.checkedArea && ['leftPt', 'topPt', 'widthPt', 'heightPt'].every(key =>
        Number.isFinite(data.checkedArea[key]) && Math.abs(data.checkedArea[key] - request.geometry[key]) < 1e-4)
      && typeof data.image === 'string' && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(data.image)
      && Number.isFinite(data.maximumCoverage) && data.maximumCoverage >= 0 && data.maximumCoverage <= inks.length * 100
      && Number.isFinite(data.areaOverLimit) && data.areaOverLimit >= 0 && data.areaOverLimit <= 100
      && Number.isInteger(data.pixelsOverLimit) && data.pixelsOverLimit >= 0
      && Number.isInteger(data.checkedPixels) && data.checkedPixels > 0 && data.pixelsOverLimit <= data.checkedPixels
      && Array.isArray(data.includedChannels) && JSON.stringify(data.includedChannels) === JSON.stringify(inks)
      && Array.isArray(data.excludedChannels) && data.excludedChannels.every(name => typeof name === 'string');
  }
  async function run(event) {
    event.preventDefault();
    if (!scope || !sameView(scope) || !ready() || channelLoading) { validate(); return; }
    const inks = chosen(), limit = Number(value.value);
    if (!inks.length || !value.checkValidity()) { validate(); return; }
    const checkCrop = form.elements.inkLimitArea.value === 'selection' ? {...state.crop} : null;
    close();
    const request = beginAnalysis('ink-limit'); if (!request) return;
    trackInk('ink_limit_requested');
    request.checkCrop = checkCrop; active = request; error = null; results.delete(request.page);
    const dims = {widthPt: state.pdfWidthPt, heightPt: state.pdfHeightPt};
    const geometryScope = {...request, crop: checkCrop};
    const [cropLeft, cropTop, cropWidth, cropHeight] = captureMeasuredGeometry(geometryScope, dims);
    request.geometry = geometryScope.geometry; sync();
    try {
      const data = await post('/api/ink-limit', {id: request.id, page: request.page, cropLeft, cropTop, cropWidth, cropHeight,
        pdfWidthPt: dims.widthPt, pdfHeightPt: dims.heightPt, channels: inks, limit}, t('limit_failed'));
      if (!analysisIsCurrent(request)) return;
      if (!validData(data, request, inks, limit)) throw new Error(t('limit_incomplete'));
      await new Promise((resolve, reject) => { const image = new Image(); image.onload = () => {
        if (image.naturalWidth !== Math.round(data.widthPt * data.dpi / 72)
          || image.naturalHeight !== Math.round(data.heightPt * data.dpi / 72)) {
          reject(new Error(t('heatmap_mismatch')));
        } else resolve();
      };
        image.onerror = () => reject(new Error(t('heatmap_failed'))); image.src = data.image; });
      if (!analysisIsCurrent(request)) return;
      results.set(request.page, {scope: request, data}); view = 'overlay';
      trackInk('ink_limit_completed', { result_status: 'complete' });
      announceStatus(data.pixelsOverLimit ? t('limit_complete') : formatText('no_areas_over_limit', {limit}));
    } catch (err) {
      if (!analysisIsCurrent(request)) return;
      trackInk('ink_limit_failed', { failure_reason: analyticsFailure(err) });
      if ([404, 410].includes(err.status)) state.sourceUnavailable = true;
      error = {scope: request, message: err.message};
    } finally {
      if (active === request) active = null;
      finishAnalysis(request); sync();
    }
  }
  function renderResult(entry) {
    panel.replaceChildren(); if (!entry) return;
    const {data, scope: owner} = entry;
    const title = resultElement('h3', 'result-group-title', t('ink_limit_check')); title.id = 'inkLimitResultsTitle';
    panel.append(title, resultElement('p', 'ink-limit-note', `${t('section_page')} ${owner.page} · ${t(owner.checkCrop ? 'current_selection' : 'whole_page')}`));
    for (const [label, reading] of [[t('ink_limit'), `${data.limit}%`], [t('maximum_coverage'), `${data.maximumCoverage.toFixed(2)}%`],
      [t('area_over_limit'), `${data.areaOverLimit.toFixed(2)}%`]]) {
      const row = resultElement('div', 'total-row');
      row.append(resultElement('span', 'total-label', label), resultElement('span', 'total-value', reading)); panel.append(row);
    }
    if (data.pixelsOverLimit && data.areaOverLimit < .005) panel.append(resultElement('p', 'ink-limit-note', t('tiny_over_limit')));
    if (!data.pixelsOverLimit) panel.append(resultElement('p', 'ink-limit-note', formatText('no_areas_over_limit', {limit: data.limit})));
    panel.append(resultElement('p', 'ink-limit-note', t('included_inks') + ': ' + data.includedChannels.join(', ')));
    if (data.excludedChannels.length) panel.append(resultElement('p', 'ink-limit-note', t('excluded_inks') + ': ' + data.excludedChannels.join(', ')));
    if (data.pixelsOverLimit) {
      const legend = resultElement('div', 'ink-limit-legend');
      legend.append(resultElement('span', 'ink-limit-gradient'), document.createTextNode(t('limit_legend'))); panel.append(legend);
    }
    panel.append(resultElement('p', 'ink-limit-note', t('rendered_estimate')));
  }
  function sync() {
    if (documentVersion !== state.documentVersion) {
      documentVersion = state.documentVersion; results.clear(); channelCache.clear(); error = null; rendered = null;
    }
    for (const [page, entry] of results) if (!sameMeasurement(entry.scope)) results.delete(page);
    if (active && !analysisIsCurrent(active)) { active = null; channelLoading = false; }
    if (dialog.open && (!scope || !sameView(scope))) close();
    const entry = current();
    button.hidden = false; button.disabled = !ready();
    button.textContent = active?.mode === 'ink-limit' ? t('checking_ink_limit') : t('check_ink_limit');
    $('#inkLimitViewTools').hidden = !entry;
    for (const [control, mode] of [[toggle, 'heatmap'], [overlayToggle, 'overlay']]) {
      control.disabled = !state.previewReady || state.isLoading || isDragging || isResizing;
      control.setAttribute('aria-pressed', String(Boolean(entry && view === mode)));
      control.title = view === mode ? t('show_document') : mode === 'heatmap' ? t('show_heatmap') : t('show_overlay');
    }
    const visible = entry && state.previewReady && !state.isLoading;
    overlay.hidden = !visible || view === 'document';
    previewContainer.classList.toggle('ink-limit-only', Boolean(visible && view === 'heatmap'));
    if (entry && overlay.getAttribute('src') !== entry.data.image) overlay.src = entry.data.image;
    if (!entry) overlay.removeAttribute('src');
    panel.hidden = !entry;
    if (rendered !== entry || renderedLang !== currentLang) { renderResult(entry); rendered = entry; renderedLang = currentLang; }
    const empty = document.querySelector('#resultsBody .results-empty-state'); if (empty) empty.hidden = Boolean(entry);
    $('#resultsTitle').textContent = entry ? t('analysis_title') : t('results_title');
    const showError = error && sameView(error.scope);
    $('#inkLimitError').hidden = !showError; $('#inkLimitError').textContent = showError ? error.message : '';
    $('#inkLimitStatus').hidden = active?.mode !== 'ink-limit';
    if (!$('#inkLimitStatus').hidden) $('#inkLimitStatus').textContent = t('limit_progress');
    if (channelLoading) status.textContent = t('loading_inks');
    if (dialog.open) validate();
    window.channelPreview?.sync();
    window.coverageReport?.sync();
  }
  button.addEventListener('click', open);
  for (const [control, mode] of [[toggle, 'heatmap'], [overlayToggle, 'overlay']]) {
    control.addEventListener('click', () => { if (current() && !control.disabled) { view = view === mode ? 'document' : mode; sync(); } });
  }
  $('#btnCloseInkLimit').addEventListener('click', close); $('#btnCancelInkLimit').addEventListener('click', close);
  form.addEventListener('submit', run); form.addEventListener('input', () => { setError(''); validate(); });
  dialog.addEventListener('close', () => {
    scope = null; button.setAttribute('aria-expanded', 'false');
    const target = state.pdfId ? (!button.hidden && !button.disabled ? button : canvasArea) : $('#btnWelcomeUpload');
    target.focus({preventScroll: true});
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const controls = [...dialog.querySelectorAll('button, input')].filter(n => !n.disabled && n.getClientRects().length && (n.type !== 'radio' || n.checked));
    const first = controls[0], last = controls.at(-1);
    if (!controls.includes(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
      event.preventDefault(); (event.shiftKey ? last : first)?.focus();
    }
  });
  window.inkLimit = {sync, reportEntry: () => current(),
    reportEntries: () => [...results.values()].filter(entry => sameMeasurement(entry.scope)),
    viewMode: () => current() ? view : 'document'}; sync();
})();
