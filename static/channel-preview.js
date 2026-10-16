// All-language view controls. Coverage/heatmap inputs and exports are untouched.
(() => {
  const canvas = $('#channelPreviewCanvas'), context = canvas.getContext('2d');
  const cache = new Map(), settings = new Map();
  let docVersion = state.documentVersion, pageVersion = state.viewVersion, black = false;
  let flight = null, message = '', failure = '', paintVersion = 0, pendingKey = null, paintedKey = null;
  const names = () => validResult(state.currentPage)?.channels.map(ch => ch.name) || [];
  const selected = () => settings.get(state.currentPage) ?? null;
  const included = name => selected() === null || selected().has(name);
  const owner = () => ({id: state.pdfId, doc: state.documentVersion, page: state.currentPage, view: state.viewVersion});
  const samePage = scope => scope.id === state.pdfId && scope.doc === state.documentVersion
    && scope.page === state.currentPage && scope.view === state.viewVersion;
  const interactive = () => state.previewReady && !state.isLoading && !state.isAnalyzing && !isDragging && !isResizing
    && (cache.has(state.currentPage) || (!state.sourceUnavailable && !sourceTimeExpired()));
  const tick = () => new Promise(resolve => requestAnimationFrame(resolve));

  function label(text, id) {
    const node = resultElement('label', 'channel-preview-control');
    const input = document.createElement('input'); input.type = 'checkbox'; input.id = id;
    node.append(input, document.createTextNode(text)); return [node, input];
  }
  function allControl() {
    const [node, input] = label(t('all_channels'), 'channelPreviewAll'); node.classList.add('channel-preview-all');
    input.checked = selected() === null;
    input.addEventListener('change', () => { settings.set(state.currentPage, input.checked ? null : new Set()); changed(); });
    return node;
  }
  function options() {
    const node = resultElement('div', 'channel-preview-options');
    const [control, input] = label(t('black_preview'), 'channelPreviewBlack'); input.checked = black;
    input.addEventListener('change', () => { black = input.checked; changed(); });
    const note = resultElement('p', 'channel-preview-note', t('channel_hint'));
    const status = resultElement('p', 'channel-preview-note'); status.id = 'channelPreviewStatus';
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.hidden = !message; status.textContent = message;
    const error = resultElement('p', ''); error.id = 'channelPreviewError'; error.setAttribute('role', 'alert');
    error.hidden = !failure; error.textContent = failure;
    node.append(control, note, status, error); return node;
  }
  function decorate(row, ch) {
    const input = document.createElement('input'); input.type = 'checkbox'; input.className = 'channel-visibility';
    input.dataset.channel = ch.name; input.checked = included(ch.name);
    input.setAttribute('aria-label', formatText('channel_show', {name: ch.name}));
    input.title = formatText('channel_toggle', {name: ch.name});
    row.dataset.channelHidden = String(!input.checked);
    input.addEventListener('click', event => {
      if (!event.altKey) return;
      event.preventDefault();
      const current = names().filter(included);
      settings.set(state.currentPage, current.length === 1 && current[0] === ch.name ? null : new Set([ch.name]));
      changed(); requestAnimationFrame(syncControls);
    });
    input.addEventListener('change', () => {
      const next = new Set(names().filter(included));
      if (input.checked) next.add(ch.name); else next.delete(ch.name);
      settings.set(state.currentPage, next.size === names().length ? null : next); changed();
    });
    // The row is also a generous pointer/touch target for its native checkbox.
    row.addEventListener('click', event => {
      if (event.target === input || input.disabled) return;
      if (event.altKey) {
        const current = names().filter(included);
        settings.set(state.currentPage, current.length === 1 && current[0] === ch.name ? null : new Set([ch.name]));
        changed();
      } else input.click();
    });
    row.prepend(input);
  }
  function changed() {
    failure = ''; message = flight ? t('channel_preparing') : ''; pendingKey = null; paintVersion++; sync();
  }
  function syncControls() {
    const enabled = interactive();
    for (const input of document.querySelectorAll('.channel-visibility')) {
      input.checked = included(input.dataset.channel); input.disabled = !enabled || Boolean(flight);
      input.closest('.ink-row').dataset.channelHidden = String(!input.checked);
    }
    const all = $('#channelPreviewAll');
    if (all) {
      const count = names().filter(included).length;
      all.checked = count === names().length; all.indeterminate = count > 0 && count < names().length;
      all.disabled = !enabled; // Can always restore the original while a render is pending.
    }
    const option = $('#channelPreviewBlack');
    if (option) { option.checked = black; option.disabled = !enabled || Boolean(flight); }
    const status = $('#channelPreviewStatus');
    if (status) { status.textContent = message; status.hidden = !message; }
    const error = $('#channelPreviewError');
    if (error) { error.textContent = failure; error.hidden = !failure; }
  }
  function validPacket(data, scope) {
    return data?.page === scope.page && data.widthPt === state.pdfWidthPt && data.heightPt === state.pdfHeightPt
      && Number.isInteger(data.dpi) && data.dpi > 0 && data.dpi <= 600
      && Number.isInteger(data.width) && Number.isInteger(data.height) && data.width > 0 && data.height > 0
      && data.width === Math.round(data.widthPt * data.dpi / 72) && data.height === Math.round(data.heightPt * data.dpi / 72)
      && data.width * data.height <= 5_000_000 && Math.max(data.width, data.height) <= 10_000
      && Array.isArray(data.channels) && data.channels.length >= 4 && data.channels.length <= 64
      && data.width * data.height * data.channels.length <= 32_000_000
      && new Set(data.channels.map(ch => ch?.name)).size === data.channels.length
      && data.channels.every(ch => typeof ch.name === 'string' && ch.name.length > 0 && ch.name.length <= 1024
        && ch.maskEncoding === 'alpha'
        && (ch.displayColor === undefined || /^#[0-9a-f]{6}$/i.test(ch.displayColor))
        && typeof ch.mask === 'string' && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(ch.mask))
      && names().every(name => data.channels.some(ch => ch.name === name))
      && data.channels.reduce((total, ch) => total + ch.mask.length, 0) <= 90_000_000;
  }
  async function loadMasks() {
    if (flight || !interactive()) return;
    const scope = owner(), controller = new AbortController(), request = {scope, controller};
    flight = request; message = t('channel_preparing'); updateActionControls();
    try {
      const response = await fetch('/api/channel-preview', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: scope.id, page: scope.page}), signal: controller.signal});
      const data = await apiResult(response, t('channel_prepare_failed'));
      if (!samePage(scope)) return;
      if (!validPacket(data, scope)) throw new Error(t('channel_mismatch'));
      const decoded = [];
      for (const ch of data.channels) {
        const pixels = await new Promise((resolve, reject) => {
          const image = new Image(); image.onload = () => {
            try {
              if (image.naturalWidth !== data.width || image.naturalHeight !== data.height) throw new Error(t('channel_incomplete'));
              const work = document.createElement('canvas'); work.width = data.width; work.height = data.height;
              const ctx = work.getContext('2d', {willReadFrequently: true}); ctx.drawImage(image, 0, 0);
              const rgba = ctx.getImageData(0, 0, data.width, data.height).data, ink = new Uint8Array(data.width * data.height);
              for (let i = 0; i < ink.length; i++) {
                const offset = i * 4;
                // Alpha is coverage data, independent of display colour profiles.
                ink[i] = rgba[offset + 3];
              }
              work.width = work.height = 1; image.src = ''; resolve(ink);
            } catch (err) { reject(err); }
          };
          image.onerror = () => reject(new Error(t('channel_read_failed'))); image.src = ch.mask;
        });
        if (!samePage(scope)) return;
        const color = ch.displayColor || '#000000';
        decoded.push({name: ch.name, pixels, color: [1, 3, 5].map(pos => parseInt(color.slice(pos, pos + 2), 16))});
      }
      cache.delete(scope.page); cache.set(scope.page, {width: data.width, height: data.height, channels: decoded});
      while (cache.size > 2) cache.delete(cache.keys().next().value);
    } catch (err) {
      if (samePage(scope) && err.name !== 'AbortError') {
        if ([404, 410].includes(err.status)) state.sourceUnavailable = true;
        failure = err.message;
      }
    } finally {
      refreshAllowance();
      if (flight === request) { flight = null; message = ''; updateActionControls(); }
    }
  }
  const key = () => JSON.stringify([state.documentVersion, state.currentPage, black, selected() === null ? 'all' : [...selected()].sort()]);
  async function paint(packet, paintKey) {
    const version = ++paintVersion, scope = owner(); pendingKey = paintKey; message = t('channel_updating'); syncControls();
    try {
      const output = new ImageData(packet.width, packet.height), rgba = output.data; rgba.fill(255);
      for (const ch of packet.channels.filter(ch => included(ch.name))) {
        const color = black ? [0, 0, 0] : ch.color;
        for (let start = 0; start < ch.pixels.length; start += 150000) {
          if (version !== paintVersion || !samePage(scope)) return;
          const end = Math.min(start + 150000, ch.pixels.length);
          for (let i = start; i < end; i++) {
            const amount = ch.pixels[i] / 255, offset = i * 4;
            for (let component = 0; component < 3; component++) rgba[offset + component] *= 1 - amount * (1 - color[component] / 255);
          }
          await tick();
        }
      }
      if (version !== paintVersion || !samePage(scope)) return;
      canvas.width = packet.width; canvas.height = packet.height; context.putImageData(output, 0, 0);
      paintedKey = paintKey;
    } catch (err) {
      if (version === paintVersion && samePage(scope)) failure = t('channel_display_failed');
    } finally {
      if (version === paintVersion) { pendingKey = null; message = ''; sync(); }
    }
  }
  function sync() {
    if (flight) message = t('channel_preparing');
    else if (pendingKey) message = t('channel_updating');
    if (docVersion !== state.documentVersion) {
      docVersion = state.documentVersion; cache.clear(); settings.clear(); black = false;
      flight?.controller.abort(); flight = null; failure = ''; message = ''; pendingKey = null; paintedKey = null; paintVersion++;
      canvas.width = canvas.height = 1;
    }
    if (pageVersion !== state.viewVersion) {
      pageVersion = state.viewVersion; flight?.controller.abort(); flight = null; failure = ''; message = ''; pendingKey = null; paintVersion++;
    }
    const show = state.previewReady && !state.isLoading && Boolean(validResult(state.currentPage));
    const altered = selected() !== null || black, paintKey = key(), packet = cache.get(state.currentPage);
    if (show && altered && !failure) {
      if (selected()?.size === 0) {
        if (paintedKey !== paintKey) { canvas.width = canvas.height = 1; context.fillStyle = '#fff'; context.fillRect(0, 0, 1, 1); paintedKey = paintKey; }
      } else if (packet) {
        if (paintedKey !== paintKey && pendingKey !== paintKey) void paint(packet, paintKey);
      } else if (!flight) void loadMasks();
    }
    const visible = show && altered && paintedKey === paintKey && window.inkLimit?.viewMode() !== 'heatmap';
    canvas.hidden = !visible; previewContainer.classList.toggle('channel-preview-active', visible);
    syncControls();
  }
  window.channelPreview = {allControl, options, decorate, sync, busy: () => Boolean(flight)}; sync();
})();
