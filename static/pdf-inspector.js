// Read-only estimator helper. Inspection never changes analyzer selections/results.
(() => {
  'use strict';
  const button = document.getElementById('btnInspectPdf');
  const dialog = document.createElement('dialog');
  const tr = (key, values = {}) => t('inspect_'+key).replace(/\{(\w+)\}/g, (all, name) => values[name] ?? all);
  let renderedLang = null;
  dialog.id = 'pdfInspectorDialog';
  dialog.setAttribute('aria-labelledby', 'inspectTitle');
  dialog.innerHTML = `
    <header class="inspect-header"><h2 id="inspectTitle">Inspect PDF</h2><span class="inspect-file" id="inspectFile"></span>
    <div class="inspect-toolbar" role="group" aria-label="Inspector tools">
      <button type="button" data-inspect-zoom="1" aria-pressed="true">Fit</button>
      <button type="button" data-inspect-zoom="2" aria-pressed="false">200%</button>
      <button type="button" data-inspect-zoom="5" aria-pressed="false">500%</button>
      <button type="button" id="inspectHand" aria-pressed="false" title="Drag the page to pan">Hand</button>
      <button type="button" id="inspectClear" aria-label="Clear selection"><span class="inspect-clear-long">Clear selection</span><span class="inspect-clear-short">Clear</span></button>
    </div>
    <button type="button" id="inspectToggleLayers" aria-controls="inspectLayers" aria-expanded="false" hidden>Layers</button>
    <button type="button" id="inspectClose" aria-label="Close PDF inspector">×</button></header>
    <div class="inspect-guidance"><span>Hover to inspect · Click to select · Alt-click to select underneath · Hand to pan</span><span id="inspectStatus" role="status" aria-live="polite"></span></div>
    <div class="inspect-body" data-layers-open="false">
      <aside id="inspectLayers" aria-label="PDF layers"><h3>Layers</h3><div id="inspectLayerList"></div></aside>
      <div id="inspectViewport" tabindex="0" role="region" aria-label="PDF objects. Use left and right arrow keys to browse objects; Enter to select. Space and drag to pan.">
        <div id="inspectStage"><div id="inspectPage" hidden><img id="inspectImage" alt="PDF page for inspection" draggable="false"><canvas id="inspectCanvas" aria-hidden="true"></canvas></div></div>
      </div>
      <aside id="inspectProperties" aria-label="Object properties"></aside>
    </div>`;
  document.body.append(dialog);
  const el = id => dialog.querySelector('#'+id);
  const viewport = el('inspectViewport'), stage = el('inspectStage'), sheet = el('inspectPage');
  const canvas = el('inspectCanvas'), image = el('inspectImage'), properties = el('inspectProperties');
  const status = el('inspectStatus'), layerList = el('inspectLayerList'), handButton = el('inspectHand');
  const body = dialog.querySelector('.inspect-body'), layerToggle = el('inspectToggleLayers');
  const context = canvas.getContext('2d'), hitContext = document.createElement('canvas').getContext('2d');
  const cache = new Map(), layerViews = new Map(), layerChoices = new Map();
  let docVersion = state.documentVersion, scope = null, packet = null, paths = [];
  let zoom = 1, scale = 1, hand = false, space = false, pan = null, dragMoved = false;
  let selected = null, hovered = null, overlap = [], overlapIndex = 0, lastPoint = null, keyboardIndex = -1;
  let generation = 0, active = null, layoutFrame = null;
  let visibleLayers = new Set(), layerBusy = false, layerReady = false;
  const samePage = opening => opening && opening.id === state.pdfId && opening.documentVersion === state.documentVersion
    && opening.page === state.currentPage && opening.viewVersion === state.viewVersion;
  const ready = () => state.pdfId && state.previewReady && !state.isLoading && !state.isAnalyzing
    && !isDragging && !isResizing && !window.channelPreview?.busy()
    && (cache.has(state.currentPage) || (!state.sourceUnavailable && !sourceTimeExpired()));
  const text = (tag, content, className) => {
    const node = document.createElement(tag); node.textContent = content;
    if (className) node.className = className;
    return node;
  };
  let statusKey = null;
  function setStatus(message = '', error = false) { statusKey = null; status.textContent = message; status.dataset.error = String(error); }
  function message(key = '', error = false) { setStatus(key ? tr(key) : '', error); statusKey = key; }
  function translateUi() {
    dialog.lang = currentLang === 'zh' ? 'zh-Hans' : currentLang;
    for (const [selector,key] of [['#inspectTitle','title'],['[data-inspect-zoom="1"]','fit'],['#inspectHand','hand'],['.inspect-clear-long','clear'],['.inspect-clear-short','clear_short'],['#inspectToggleLayers','layers'],['.inspect-guidance > span:first-child','hint'],['#inspectLayers h3','layers']]) dialog.querySelector(selector).textContent=tr(key);
    for (const [selector,key] of [['.inspect-toolbar','tools'],['#inspectClear','clear'],['#inspectClose','close'],['#inspectLayers','layers_aria'],['#inspectViewport','viewport'],['#inspectProperties','properties']]) dialog.querySelector(selector).setAttribute('aria-label',tr(key));
    handButton.title=tr('pan_title'); image.alt=tr('image_alt');
    if (scope) el('inspectFile').textContent=tr('page',{name:state.fileName,page:scope.page,count:state.pageCount});
    if (statusKey) status.textContent=tr(statusKey);
    renderedLang=currentLang; renderLayers();
  }
  function colorLabel(color) {
    const percent = number => `${Number((number * 100).toFixed(2))}%`;
    if (color.kind === 'None') return tr('none');
    if (color.kind === 'CMYK') return color.values.map((v, i) => `${['C', 'M', 'Y', 'K'][i]} ${percent(v)}`).join(' · ');
    if (color.kind === 'RGB') return 'RGB '+color.values.map(v => Math.round(v*255)).join(', ');
    if (color.kind === 'Gray') return tr('gray',{value:percent(1-color.values[0])});
    if (color.kind === 'Spot' || color.kind === 'DeviceN') return color.inks.map(ink => `${ink.name} · ${percent(ink.tint)}`).join(' / ');
    return tr('unavailable');
  }
  function size(value) { return state.units === 'in' ? (value/72).toFixed(3) : (value*25.4/72).toFixed(2); }
  function unit() { return state.units === 'in' ? 'in' : 'mm'; }
  function showProperties() {
    properties.replaceChildren();
    const item = selected || hovered;
    properties.append(text('p', tr(selected ? 'selected' : hovered ? 'hovered' : 'properties'), 'inspect-eyebrow'));
    if (!item) {
      properties.append(text('h3', tr('find'), 'inspect-object-title'),
        text('p', tr('empty'), 'inspect-empty'), text('p', tr('selectable'), 'inspect-selection-note'), text('p', tr('keyboard'), 'inspect-selection-note'));
      return;
    }
    properties.append(text('h3', `${tr('type_'+item.type)} ${item.id+1}`, 'inspect-object-title'));
    const [x0, y0, x1, y1] = item.bounds;
    properties.append(text('p', `${size(x1-x0)} × ${size(y1-y0)} ${unit()}`, 'inspect-size'),
      text('p', tr('size_label'), 'inspect-size-label'));
    const fields = document.createElement('dl'); fields.className = 'inspect-fields';
    const field = (name, value) => fields.append(text('dt', name), text('dd', value));
    field(tr('fill'), colorLabel(item.fill)); field(tr('stroke'), colorLabel(item.stroke));
    if (item.stroke.kind !== 'None') field(tr('thickness'), item.lineWidthPt === null ? tr('varies') : item.lineWidthPt === 0 ? tr('hairline') : `${size(item.lineWidthPt)} ${unit()}`);
    if (item.layer !== null) field(tr('layer'), packet.layers.find(l => l.id === item.layer)?.name || tr('unnamed'));
    properties.append(fields);
    if (item.colorWarning) properties.append(text('p', tr('color_warning'), 'inspect-warning'));
    if (item.geometryWarning) properties.append(text('p', tr('geometry_warning'), 'inspect-warning'));
    if (item.type === 'Path') properties.append(text('p', tr('source_note'), 'inspect-selection-note'));
    if (selected && overlap.length > 1) {
      const row = document.createElement('div'); row.className = 'inspect-overlap';
      row.append(text('span', tr('overlap',{index:overlapIndex+1,count:overlap.length})));
      const next = text('button', tr('next')); next.type = 'button'; next.id = 'inspectNextObject';
      next.addEventListener('click', () => { overlapIndex = (overlapIndex+1)%overlap.length; selected = overlap[overlapIndex]; showProperties(); draw(); el('inspectNextObject')?.focus(); });
      row.append(next); properties.append(row);
    }
  }
  function makePath(commands) {
    const path = new Path2D();
    for (const [op, ...args] of commands) {
      if (op === 'M') path.moveTo(...args);
      else if (op === 'L') path.lineTo(...args);
      else if (op === 'C') path.bezierCurveTo(...args);
      else path.closePath();
    }
    return path;
  }
  function draw() {
    if (!packet || !sheet.clientWidth) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    // Cap backing-store size at high zoom; geometry/hit tests stay in PDF units.
    const factor = Math.min(ratio, 5000/Math.max(sheet.clientWidth, sheet.clientHeight));
    canvas.width = Math.max(1, Math.round(sheet.clientWidth*factor));
    canvas.height = Math.max(1, Math.round(sheet.clientHeight*factor));
    context.setTransform(canvas.width/packet.widthPt, 0, 0, canvas.height/packet.heightPt, 0, 0);
    const show = (item, pinned) => {
      if (!item || !allowed(item)) return;
      const entry = paths.find(entry => entry.item === item);
      if (!entry) return;
      context.save();
      context.beginPath(); context.rect(item.clip[0], item.clip[1], item.clip[2]-item.clip[0], item.clip[3]-item.clip[1]); context.clip();
      context.strokeStyle = pinned ? '#255bff' : '#ff8a18'; context.fillStyle = pinned ? '#3565ff18' : '#ff8a1818';
      context.lineWidth = 2/scale; context.setLineDash([]);
      if (item.fill.kind !== 'None') context.fill(entry.path, item.evenOdd ? 'evenodd' : 'nonzero');
      context.stroke(entry.path);
      context.restore();
      context.save(); context.strokeStyle = pinned ? '#255bff' : '#ff8a18'; context.lineWidth = 1/scale; context.setLineDash([4/scale, 4/scale]);
      const [x, y, right, bottom] = item.bounds; context.strokeRect(x, y, right-x, bottom-y); context.restore();
    };
    if (hovered !== selected) show(hovered, false);
    show(selected, true);
  }
  function layout(center) {
    if (!packet || !dialog.open) return;
    const availableWidth = Math.max(1, viewport.clientWidth-48), availableHeight = Math.max(1, viewport.clientHeight-48);
    scale = Math.min(availableWidth/packet.widthPt, availableHeight/packet.heightPt)*zoom;
    const width = packet.widthPt*scale, height = packet.heightPt*scale;
    sheet.style.width = `${width}px`; sheet.style.height = `${height}px`;
    stage.style.width = `${width+48}px`; stage.style.height = `${height+48}px`;
    if (zoom === 1) viewport.scrollTo(0, 0);
    else if (center) viewport.scrollTo(sheet.offsetLeft+center.x*width-viewport.clientWidth/2,
      sheet.offsetTop+center.y*height-viewport.clientHeight/2);
    draw();
  }
  function scheduleLayout() {
    if (layoutFrame !== null) return;
    layoutFrame = requestAnimationFrame(() => { layoutFrame = null; layout(); });
  }
  function allowed(item) { return item.layers.every(id => visibleLayers.has(id)); }
  function hit(x, y) {
    if (layerBusy || !layerReady) return [];
    const tolerance = 5/scale;
    return paths.filter(({item, path}) => {
      if (!allowed(item)) return false;
      const b = item.bounds, clip = item.clip;
      if (x < clip[0] || y < clip[1] || x > clip[2] || y > clip[3]
          || x < b[0]-tolerance || y < b[1]-tolerance || x > b[2]+tolerance || y > b[3]+tolerance) return false;
      if (item.fill.kind !== 'None' && hitContext.isPointInPath(path, x, y, item.evenOdd ? 'evenodd' : 'nonzero')) return true;
      hitContext.lineWidth = Math.max(tolerance*2, item.lineWidthPt || 0);
      return item.stroke.kind !== 'None' && hitContext.isPointInStroke(path, x, y);
    }).reverse().map(entry => entry.item);
  }
  function location(event) {
    const box = sheet.getBoundingClientRect();
    return {x: (event.clientX-box.left)/scale, y: (event.clientY-box.top)/scale};
  }
  function controls() {
    for (const b of dialog.querySelectorAll('[data-inspect-zoom]')) b.setAttribute('aria-pressed', String(Number(b.dataset.inspectZoom) === zoom));
    handButton.setAttribute('aria-pressed', String(hand)); viewport.dataset.hand = String(hand || space);
    el('inspectClear').disabled = !selected;
    for (const b of dialog.querySelectorAll('.inspect-toolbar button')) if (b.id !== 'inspectClear') b.disabled = !packet;
    const expired = state.sourceUnavailable || sourceTimeExpired();
    for (const b of layerList.querySelectorAll('button')) b.disabled = layerBusy || expired;
    viewport.setAttribute('aria-busy', String(layerBusy));
    sheet.classList.toggle('inspect-layer-loading', layerBusy);
  }
  function clear() { selected = hovered = null; overlap = []; overlapIndex = 0; lastPoint = null; keyboardIndex = -1; showProperties(); draw(); controls(); }
  function validPacket(data, opening) {
    const finite = n => Number.isFinite(n) && Math.abs(n) <= 1e9;
    const color = value => value && (value.kind === 'None' || value.kind === 'unavailable'
      || ['CMYK', 'RGB', 'Gray'].includes(value.kind) && Array.isArray(value.values)
        && value.values.length === ({CMYK:4, RGB:3, Gray:1}[value.kind]) && value.values.every(n => finite(n) && n >= 0 && n <= 1)
      || ['Spot', 'DeviceN'].includes(value.kind) && Array.isArray(value.inks) && value.inks.length <= 32
        && value.inks.every(ink => typeof ink.name === 'string' && ink.name.length <= 128 && finite(ink.tint) && ink.tint >= 0 && ink.tint <= 1));
    return data && data.page === opening.page && finite(data.widthPt) && data.widthPt > 0 && finite(data.heightPt) && data.heightPt > 0
      && Array.isArray(data.layers) && data.layers.length <= 128 && data.layers.every(l => typeof l.id === 'string' && /^[0-9]{1,16}$/.test(l.id) && typeof l.name === 'string' && l.name.length <= 128 && typeof l.visible === 'boolean')
      && Array.isArray(data.objects) && data.objects.length <= 1500 && data.objects.every(o =>
        Number.isInteger(o.id) && ['Path', 'Image', 'Gradient'].includes(o.type)
        && Array.isArray(o.bounds) && o.bounds.length === 4 && o.bounds.every(finite)
        && Array.isArray(o.clip) && o.clip.length === 4 && o.clip.every(finite)
        && (o.lineWidthPt === null || finite(o.lineWidthPt) && o.lineWidthPt >= 0)
        && (o.layer === null || data.layers.some(l => l.id === o.layer))
        && Array.isArray(o.layers) && o.layers.length <= 64 && o.layers.every(id => data.layers.some(l => l.id === id))
        && color(o.fill) && color(o.stroke) && Array.isArray(o.path) && o.path.length <= 18000
        && o.path.every(c => Array.isArray(c) && c.length === ({M:3, L:3, C:7, Z:1}[c[0]]) && c.slice(1).every(finite)));
  }
  function present(data) {
    packet = data; paths = data.objects.map(item => ({item, path: makePath(item.path)}));
    visibleLayers = new Set(layerChoices.get(scope.page) || data.layers.filter(l => l.visible).map(l => l.id));
    layerReady = !data.layers.length;
    renderLayers();
    sheet.hidden = false; clear(); controls();
    const messages = [];
    if (data.limited) messages.push(tr('partial'));
    if (!data.objects.length) messages.push(tr('no_objects'));
    setStatus(messages.join(' ')); layout();
  }
  const eye = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>`;
  const eyeOff = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m3 3 18 18M10.5 5.1 12 5c6.5 0 10 7 10 7a23 23 0 0 1-3.2 3.9M6.2 6.2A24 24 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5.8-1.6M10 10a3 3 0 0 0 4 4"/></svg>`;
  function renderLayers() {
    const focused = layerList.contains(document.activeElement) ? document.activeElement.dataset.layer : null;
    layerList.replaceChildren();
    layerToggle.hidden = !packet?.layers.length;
    if (!packet) { layerList.append(text('p', tr('reading_layers'), 'inspect-layer-note')); return; }
    if (!packet.layers.length) { layerList.append(text('p', tr('no_layers'), 'inspect-layer-note')); return; }
    for (const entry of packet.layers) {
      const shown = visibleLayers.has(entry.id);
      const row = document.createElement('button'); row.type = 'button'; row.className = 'inspect-layer-row'; row.dataset.layer = entry.id;
      row.setAttribute('role', 'switch'); row.setAttribute('aria-checked', String(shown));
      row.setAttribute('aria-label', tr('visibility',{name:entry.name})); row.title = tr(shown ? 'hide' : 'show',{name:entry.name});
      const icon = document.createElement('span'); icon.className = 'inspect-eye'; icon.innerHTML = shown ? eye : eyeOff;
      row.append(icon, text('span', entry.name, 'inspect-layer-name'));
      row.addEventListener('click', () => {
        if (layerBusy) return;
        const next = new Set(visibleLayers);
        if (next.has(entry.id)) next.delete(entry.id); else next.add(entry.id);
        void showLayers(next);
      });
      layerList.append(row);
    }
    controls();
    if (focused) [...layerList.querySelectorAll('button')].find(b => b.dataset.layer === focused)?.focus();
  }
  async function showLayers(next) {
    if (!packet || !scope || !dialog.open || layerBusy || !packet.layers.length) return;
    const opening = scope, ticket = generation;
    const hidden = packet.layers.filter(l => !next.has(l.id)).map(l => l.id).sort();
    const key = `${opening.page}:${hidden.join(',')}`;
    const focusedLayer = layerList.contains(document.activeElement) ? document.activeElement.dataset.layer : null;
    let request = null;
    layerBusy = true; controls();
    try {
      let url = layerViews.get(key);
      if (!url) {
        if (state.sourceUnavailable || sourceTimeExpired()) throw new Error(tr('reupload'));
        request = beginAnalysis('inspect-layers');
        if (!request) throw new Error(tr('busy'));
        active = request; message('updating');
        const data = await apiResult(await fetch(`${API}/api/inspect/layers`, {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({id:opening.id, page:opening.page, hiddenLayers:hidden})}), tr('layer_failed'));
        if (!samePage(opening) || ticket !== generation || !dialog.open) return;
        if (data.page !== opening.page || data.widthPt !== packet.widthPt || data.heightPt !== packet.heightPt
            || !Array.isArray(data.hiddenLayers) || JSON.stringify(data.hiddenLayers) !== JSON.stringify(hidden)
            || typeof data.image !== 'string' || !data.image.startsWith('data:image/png;base64,')) throw new Error(tr('layer_invalid'));
        url = data.image;
      }
      const decoded = new Image(); decoded.src = url; await decoded.decode();
      if (!samePage(opening) || ticket !== generation || !dialog.open) return;
      image.src = url; await image.decode();
      if (!samePage(opening) || ticket !== generation || !dialog.open) return;
      layerViews.set(key, url); while (layerViews.size > 4) layerViews.delete(layerViews.keys().next().value);
      visibleLayers = next; layerChoices.set(opening.page, [...next]); layerReady = true;
      if (selected && !allowed(selected)) selected = null;
      if (hovered && !allowed(hovered)) hovered = null;
      overlap = overlap.filter(allowed); overlapIndex = Math.max(0, overlap.indexOf(selected)); keyboardIndex = -1;
      renderLayers(); showProperties(); draw();
      message(packet.limited ? 'partial' : '');
    } catch (error) {
      if (!samePage(opening) || ticket !== generation || !dialog.open) return;
      if ([404, 410].includes(error.status)) state.sourceUnavailable = true;
      setStatus(error.status === 422 ? tr('layer_failed') : error.message, true);
    } finally {
      if (ticket === generation) {
        layerBusy = false; controls();
        if (focusedLayer && dialog.open && (document.activeElement === document.body || document.activeElement === dialog || layerList.contains(document.activeElement))) {
          [...layerList.querySelectorAll('button')].find(b => b.dataset.layer === focusedLayer)?.focus({preventScroll:true});
        }
      }
      if (active === request) active = null;
      if (request) { finishAnalysis(request); refreshAllowance(); }
    }
  }
  async function open() {
    if (!ready() || dialog.open) return;
    scope = captureScope(); const opening = scope, ticket = ++generation;
    packet = null; paths = []; zoom = 1; hand = space = false; sheet.hidden = true; layerBusy = layerReady = false;
    el('inspectFile').textContent = tr('page',{name:state.fileName,page:opening.page,count:state.pageCount});
    image.src = previewImg.src; clear(); controls();
    renderLayers();
    button.setAttribute('aria-expanded', 'true'); dialog.showModal(); el('inspectClose').focus();
    if (cache.has(opening.page)) { present(cache.get(opening.page)); void showLayers(new Set(visibleLayers)); return; }
    message('reading');
    const request = beginAnalysis('inspect');
    if (!request) { message('busy_open',true); return; }
    active = request;
    try {
      const response = await fetch(`${API}/api/inspect`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:opening.id, page:opening.page})});
      const data = await apiResult(response, tr('read_failed'));
      if (!samePage(opening) || ticket !== generation || !dialog.open) return;
      if (!validPacket(data, opening)) throw new Error(tr('data_invalid'));
      cache.set(opening.page, data);
      while (cache.size > 2) cache.delete(cache.keys().next().value);
      present(data);
    } catch (error) {
      if (!samePage(opening) || ticket !== generation || !dialog.open) return;
      if ([404, 410].includes(error.status)) state.sourceUnavailable = true;
      setStatus(error.status === 422 ? tr('read_failed') : error.message, true);
    } finally {
      if (active === request) active = null;
      finishAnalysis(request); refreshAllowance(); sync();
    }
    if (samePage(opening) && ticket === generation && dialog.open && packet) void showLayers(new Set(visibleLayers));
  }
  function close() { if (dialog.open) dialog.close(); }
  dialog.addEventListener('close', () => {
    generation++; scope = null; packet = null; paths = []; pan = null; hand = space = false;
    image.removeAttribute('src'); canvas.width = canvas.height = 1;
    selected = hovered = null; overlap = []; lastPoint = null;
    layerBusy = layerReady = false; visibleLayers.clear();
    button.setAttribute('aria-expanded', 'false'); sync();
    if (!button.disabled) button.focus();
  });
  button.addEventListener('click', open); el('inspectClose').addEventListener('click', close);
  el('inspectClear').addEventListener('click', clear);
  handButton.addEventListener('click', () => { hand = !hand; controls(); });
  layerToggle.addEventListener('click', () => {
    const open = body.dataset.layersOpen !== 'true';
    body.dataset.layersOpen = String(open); layerToggle.setAttribute('aria-expanded', String(open)); scheduleLayout();
  });
  for (const b of dialog.querySelectorAll('[data-inspect-zoom]')) b.addEventListener('click', () => {
    const box = sheet.getBoundingClientRect(), view = viewport.getBoundingClientRect();
    const center = {x:(view.left+viewport.clientWidth/2-box.left)/box.width, y:(view.top+viewport.clientHeight/2-box.top)/box.height};
    zoom = Number(b.dataset.inspectZoom); layout(center); controls();
  });
  viewport.addEventListener('pointermove', event => {
    if (pan && pan.id === event.pointerId) {
      const dx = event.clientX-pan.x, dy = event.clientY-pan.y;
      if (Math.abs(dx)+Math.abs(dy) > 4) dragMoved = true;
      viewport.scrollTo(pan.left-dx, pan.top-dy); event.preventDefault(); return;
    }
    if (!packet || hand || space || event.pointerType === 'touch') return;
    const p = location(event), next = hit(p.x, p.y)[0] || null;
    if (next !== hovered) { hovered = next; showProperties(); draw(); }
  });
  viewport.addEventListener('pointerleave', () => { if (!pan) { hovered = null; showProperties(); draw(); } });
  viewport.addEventListener('pointerdown', event => {
    dragMoved = false;
    if (!packet || !(hand || space) || event.button !== 0) return;
    pan = {id:event.pointerId, x:event.clientX, y:event.clientY, left:viewport.scrollLeft, top:viewport.scrollTop};
    viewport.setPointerCapture(event.pointerId); viewport.dataset.dragging = 'true'; event.preventDefault();
  });
  function endPan() {
    if (pan && viewport.hasPointerCapture(pan.id)) viewport.releasePointerCapture(pan.id);
    pan = null; viewport.dataset.dragging = 'false';
  }
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) viewport.addEventListener(type, endPan);
  viewport.addEventListener('click', event => {
    if (!packet || layerBusy || !layerReady || hand || space || dragMoved || !sheet.contains(event.target)) return;
    const p = location(event), items = hit(p.x, p.y);
    const repeat = lastPoint && Math.hypot(p.x-lastPoint.x, p.y-lastPoint.y)*scale < 5;
    const previous = selected;
    overlap = items; overlapIndex = event.altKey && repeat && items.includes(previous) ? (items.indexOf(previous)+1)%items.length : 0;
    selected = items[overlapIndex] || null; hovered = null; lastPoint = p;
    showProperties(); draw(); controls();
  });
  viewport.addEventListener('keydown', event => {
    if (['ArrowLeft', 'ArrowRight'].includes(event.key) && packet && layerReady && !layerBusy && event.target === viewport) {
      const items = paths.map(p => p.item).filter(allowed);
      const next = keyboardIndex+(event.key === 'ArrowLeft' ? -1 : 1);
      event.preventDefault();
      if (next >= 0 && next < items.length) {
        keyboardIndex = next; hovered = items[next]; showProperties(); draw();
        const [x0, y0, x1, y1] = hovered.bounds;
        if (zoom !== 1) viewport.scrollTo(sheet.offsetLeft+(x0+x1)/2*scale-viewport.clientWidth/2, sheet.offsetTop+(y0+y1)/2*scale-viewport.clientHeight/2);
      }
    } else if (event.key === 'Enter' && hovered) {
      event.preventDefault(); selected = hovered; overlap = []; showProperties(); draw(); controls();
    } else if (event.code === 'Space') { event.preventDefault(); space = true; controls(); }
  });
  dialog.addEventListener('keyup', event => { if (event.code === 'Space') { space = false; endPan(); controls(); } });
  window.addEventListener('blur', () => { space = false; endPan(); controls(); });
  image.addEventListener('load', scheduleLayout);
  new ResizeObserver(scheduleLayout).observe(viewport);
  function sync() {
    if (renderedLang !== currentLang) translateUi();
    if (docVersion !== state.documentVersion) { docVersion = state.documentVersion; cache.clear(); layerViews.clear(); layerChoices.clear(); close(); }
    if (dialog.open && !samePage(scope)) close();
    button.disabled = !ready();
    button.hidden = !state.pdfId;
    if (dialog.open && packet) showProperties();
  }
  window.pdfInspector = {sync}; sync();
})();
