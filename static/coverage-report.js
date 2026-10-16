// Document report, generated entirely in the browser from completed readings.
(() => {
  const button = $('#btnDownloadReport');
  let busy = false, library = null, coverage = null, noto = null, logo = null;
  const previews = new Map();
  let previewVersion = state.documentVersion;
  const limitEntries = () => window.inkLimit?.reportEntries() || [];
  const reportPages = () => [...new Set([...validResultPages(), ...limitEntries().map(entry => entry.scope.page)])].sort((a, b) => a - b);
  const available = () => state.pdfId && !state.isLoading && !state.isAnalyzing
    && !isDragging && !isResizing && reportPages().length > 0;
  async function retainPreview(id, version, page, data) {
    if (previewVersion !== state.documentVersion) { previews.clear(); previewVersion = state.documentVersion; }
    if (id !== state.pdfId || version !== state.documentVersion) return;
    // Keep only small thumbnails from previews already required by analysis.
    // At most 100 x 480² pixels; cap encoded storage at 16 MiB as well.
    try {
      const img = await new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image);
        image.onerror = reject; image.src = data.image; });
      if (id !== state.pdfId || version !== state.documentVersion) return;
      const scale = Math.min(480 / img.naturalWidth, 480 / img.naturalHeight, 1);
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const image = canvas.toDataURL('image/png');
      const size = [...previews].reduce((sum, [key, entry]) => sum + (key === page ? 0 : entry.image.length), image.length);
      if (size <= 16 * 1024 * 1024 && (previews.has(page) || previews.size < 100)) previews.set(page, {image, widthPt: data.widthPt, heightPt: data.heightPt});
    } catch { /* Missing thumbnails never discard completed measurements. */ }
  }
  function sync() {
    if (previewVersion !== state.documentVersion) { previews.clear(); previewVersion = state.documentVersion; }
    button.hidden = false; button.disabled = busy || !available();
    button.querySelector('span').textContent = busy ? t('report_preparing') : t('report_button');
    button.setAttribute('aria-busy', String(busy));
  }
  function script(path) {
    return new Promise((resolve, reject) => {
      const element = document.createElement('script'); element.src = path;
      element.onload = resolve; element.onerror = () => { element.remove(); reject(new Error(t('report_tools_failed'))); };
      document.head.append(element);
    });
  }
  async function tools() {
    if (!library) library = (async () => {
      await script('/static/vendor/pdfmake/pdfmake.min.js');
      await script('/static/vendor/pdfmake/vfs_fonts.js');
      const response = await fetch('/static/vendor/pdfmake/font-coverage.json');
      if (!response.ok) throw new Error(t('report_fonts_failed'));
      coverage = await response.json();
      const logoResponse = await fetch('/static/icon.svg');
      if (!logoResponse.ok) throw new Error(t('report_logo_failed'));
      logo = await logoResponse.text();
      pdfMake.addFonts({Roboto: {normal: 'Roboto-Regular.ttf', bold: 'Roboto-Medium.ttf',
        italics: 'Roboto-Italic.ttf', bolditalics: 'Roboto-MediumItalic.ttf'}});
    })().catch(error => { library = null; throw error; });
    await library;
  }
  const supports = (font, char) => coverage[font].some(([first, last]) => char >= first && char <= last);
  function text(value, options = {}) {
    const raw = String(value);
    const runs = [];
    // Choose per-character fallback so mixed Latvian/Cyrillic/CJK names survive.
    for (const c of raw) {
      const point = c.codePointAt(0);
      const visible = point >= 32 && !/[\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2066-\u2069]/u.test(c);
      const font = visible && supports('Roboto', point) ? 'Roboto' : visible && supports('NotoSansSC', point) ? 'NotoSansSC' : 'Roboto';
      const value = visible && supports(font, point) ? c : `[U+${point.toString(16).toUpperCase().padStart(4, '0')}]`;
      if (runs.at(-1)?.font === font) runs.at(-1).text += value;
      else runs.push({text: value, font});
    }
    return {text: runs, wordBreak: 'break-all', ...options};
  }
  async function fonts(nodes) {
    const needed = nodes.some(node => node.text.some(run => run.font === 'NotoSansSC'));
    if (!needed) return;
    if (!noto) noto = (async () => {
      const response = await fetch('/static/vendor/pdfmake/NotoSansSC-Regular.ttf');
      if (!response.ok) throw new Error(t('report_fonts_failed'));
      const bytes = new Uint8Array(await response.arrayBuffer());
      let binary = ''; for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      pdfMake.addVirtualFileSystem({'NotoSansSC-Regular.ttf': btoa(binary)});
      pdfMake.addFonts({NotoSansSC: {normal: 'NotoSansSC-Regular.ttf', bold: 'NotoSansSC-Regular.ttf',
        italics: 'NotoSansSC-Regular.ttf', bolditalics: 'NotoSansSC-Regular.ttf'}});
    })().catch(error => { noto = null; throw error; });
    await noto;
  }
  function snapshot() {
    const heats = new Map(limitEntries().map(entry => [entry.scope.page, entry]));
    const pages = reportPages().map(page => {
      const average = validResult(page), heat = heats.get(page) || null;
      const geometry = average ? state.resultScopes[page]?.geometry : heat?.scope.geometry;
      if (!geometry || !['leftPt', 'topPt', 'widthPt', 'heightPt'].every(key => Number.isFinite(geometry[key]))
        || geometry.widthPt <= 0 || geometry.heightPt <= 0) {
        throw new Error(formatText('report_geometry_failed', {page}));
      }
      const cached = previews.get(page), dims = state.pageDimensions[page] || cached;
      const image = page === state.currentPage && state.previewReady ? previewImg.src : cached?.image;
      return {scope: captureScope(page), averageOwner: average, heatOwner: heat,
        average: average ? structuredClone(average) : null, heat: heat ? structuredClone(heat) : null,
        colorInfo: structuredClone(average?.colorInfo || state.pageColors[page] || null),
        geometry: {...geometry}, widthPt: dims?.widthPt, heightPt: dims?.heightPt, image};
    });
    return {scope: captureScope(), lang: currentLang, pages, summary: validResultPages().length ? exportSummary(validResultPages()) : [],
      fileName: state.fileName || 'Document', filename: resultsFilename(reportPages()).replace(/_coverage\.csv$/, '_ink-coverage-report.pdf'),
      pageCount: state.pageCount, units: state.units, image: previewImg.src, created: new Date(),
      processColors: Object.fromEntries(['Cyan', 'Magenta', 'Yellow', 'Black'].map(name => {
        const variable = /var\(([^)]+)\)/.exec(getInkColor(name));
        const color = variable ? getComputedStyle(document.body).getPropertyValue(variable[1]).trim() : getInkColor(name);
        return [name, color || '#888'];
      }))};
  }
  const stillCurrent = report => currentLang === report.lang && sameView(report.scope)
    && JSON.stringify(reportPages()) === JSON.stringify(report.pages.map(page => page.scope.page))
    && report.pages.every(page => validResult(page.scope.page) === page.averageOwner
      && (limitEntries().find(entry => entry.scope.page === page.scope.page) || null) === page.heatOwner);
  async function thumbnail(report, geometry, overlay = null) {
    if (!report.image) return null;
    const image = await new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(t('report_preview_failed'))); img.src = report.image; });
    const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
    const scale = Math.min(480 / image.naturalWidth, 480 / image.naturalHeight, 1);
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    if (overlay) {
      const map = await new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(t('report_map_failed'))); img.src = overlay; });
      ctx.drawImage(map, 0, 0, canvas.width, canvas.height);
    }
    if (geometry.selected) {
      ctx.strokeStyle = '#4271c7'; ctx.lineWidth = 2;
      ctx.strokeRect(geometry.leftPt / report.widthPt * canvas.width, geometry.topPt / report.heightPt * canvas.height,
        geometry.widthPt / report.widthPt * canvas.width, geometry.heightPt / report.heightPt * canvas.height);
    }
    return canvas.toDataURL('image/png');
  }
  const heading = label => ({text: label, fontSize: 9, bold: true, color: '#888', margin: [0, 0, 0, 5]});
  function definition(report, preview, heatPreview) {
    const factor = report.units === 'in' ? 1 / 72 : 25.4 / 72, digits = report.units === 'in' ? 3 : 1;
    const unit = report.units === 'in' ? 'in' : 'mm';
    const size = area => `${(area.widthPt * factor).toFixed(digits)} × ${(area.heightPt * factor).toFixed(digits)} ${unit}`;
    const areaUnit = report.units === 'in' ? 'in²' : 'cm²';
    const areaValue = report.geometry.widthPt * report.geometry.heightPt * (report.units === 'in' ? 1 / 72 ** 2 : (2.54 / 72) ** 2);
    const customText = [], field = (value, options) => { const node = text(value, options); customText.push(node); return node; };
    const sections = [
      {columns: [{width: '*', stack: [{svg: logo, width: 24, height: 24, link: 'https://inkcoverage.app'}]},
        {width: 'auto', text: t('report_title'), fontSize: 20, bold: true, alignment: 'right', color: '#1a1a1a'}], margin: [0, 0, 0, 12]},
      field(report.fileName, {fontSize: 10, bold: true, margin: [0, 0, 0, 3]}),
      {text: t('report_created') + ': ' + report.created.toLocaleString({en:'en-GB', es:'es-ES', pt:'pt-PT', zh:'zh-CN', de:'de-DE', fr:'fr-FR', uk:'uk-UA'}[report.lang], {dateStyle: 'short', timeStyle: 'short'}), fontSize: 9, color: '#888', margin: [0, 0, 0, 18]},
      {columns: [{width: 129, stack: [preview ? {image: preview, fit: [113, 113], alignment: 'center'}
        : {text: t('report_preview_unavailable'), fontSize: 8, color: '#888', alignment: 'center'}], margin: [0, 0, 16, 0]},
        {width: '*', stack: [heading(report.average ? t('report_measured_area') : t('report_check_area')),
          {text: size(report.geometry), fontSize: 13, bold: true, margin: [0, 0, 0, 3]},
          {text: `${formatText('report_page', {page: report.scope.page, total: report.pageCount})} · ${t(report.geometry.selected ? 'selected_area' : 'whole_page')}`, fontSize: 9, color: '#888', margin: [0, 0, 0, 3]},
          {text: `${t('report_area')}: ${areaValue.toFixed(2)} ${areaUnit}`, fontSize: 9, margin: [0, 0, 0, 3]},
          ...(report.geometry.selected ? [{text: `${t('report_position')}: ${(report.geometry.leftPt * factor).toFixed(digits)} × ${(report.geometry.topPt * factor).toFixed(digits)} ${unit}`, fontSize: 9, color: '#888', margin: [0, 0, 0, 3]}] : []),
          {text: report.geometry.selected ? t('report_rectangle') : t('report_whole_measured'), fontSize: 8, color: '#888'}]}], margin: [0, 0, 0, 18]}
    ];
    if (report.average) {
      const rows = [[{text: t('report_ink'), colSpan: 2, fontSize: 9, bold: true, color: '#888'}, {},
        {text: t('report_average'), fontSize: 9, bold: true, color: '#888', alignment: 'right'}]];
      const cmyk = report.average.channels.filter(ch => !isSpotColor(ch.name));
      const spots = report.average.channels.filter(ch => isSpotColor(ch.name));
      const order = {Cyan: 0, Magenta: 1, Yellow: 2, Black: 3}; cmyk.sort((a, b) => (order[a.name] ?? 4) - (order[b.name] ?? 4));
      for (const [channels, title] of [[cmyk, t('group_cmyk')], [spots, t('group_spot')]]) {
        if (!channels.length) continue;
        rows.push([{text: title, colSpan: 2, fontSize: 8, bold: true, color: '#888'}, {}, '']);
        for (const ch of channels) {
          const known = /^#[0-9a-f]{6}$/i.test(ch.displayColor || ''), color = known ? ch.displayColor : isSpotColor(ch.name) ? '#bbb' : report.processColors[ch.name] || '#888';
          rows.push([{canvas: [{type: 'rect', x: 0, y: 0, w: 8, h: 8, color, lineColor: '#aaa', lineWidth: .4}], margin: [0, 3, 0, 0]},
            field(ch.name, {fontSize: 10}), {text: ch.coverage.toFixed(2) + '%', fontSize: 10, alignment: 'right'}]);
        }
      }
      for (const [label, value] of [[t('total_cmyk'), report.average.cmykTotal], [t('total_spot'), report.average.spotTotal], [t('total_grand'), report.average.grandTotal]]) {
        rows.push([{text: label, colSpan: 2, bold: true, fontSize: 10}, {}, {text: value.toFixed(2) + '%', bold: true, fontSize: 10, alignment: 'right'}]);
      }
      sections.push({table: {headerRows: 1, dontBreakRows: true, widths: [10, '*', 95], body: rows},
        layout: {hLineWidth: (i, node) => i === 0 || i === 1 || i === node.table.body.length ? 1 : .5,
          vLineWidth: () => 0, hLineColor: () => '#ddd', paddingLeft: () => 4, paddingRight: () => 4, paddingTop: () => 4, paddingBottom: () => 4}, margin: [0, 0, 0, 18]});
    } else sections.push({text: t('report_heat_only'), fontSize: 9, color: '#666', margin: [0, 0, 0, 18]});
    if (report.heat) {
      const data = report.heat.data, geometry = report.heat.scope.geometry;
      sections.push(heading(t('ink_limit_check')), {columns: [
        {width: 129, stack: [heatPreview ? {image: heatPreview, fit: [113, 113], alignment: 'center'}
          : {text: t('report_preview_unavailable'), fontSize: 8, color: '#888', alignment: 'center'}], margin: [0, 0, 16, 0]},
        {width: '*', stack: [
          {text: `${geometry.selected ? t('selected_area') : t('whole_page')} · ${size(geometry)}`, fontSize: 9, color: '#888', margin: [0, 0, 0, 5]},
          {text: `${t('ink_limit')}: ${data.limit}%`, fontSize: 10, margin: [0, 0, 0, 3]},
          {text: `${t('maximum_coverage')}: ${data.maximumCoverage.toFixed(2)}%`, fontSize: 10, bold: true, margin: [0, 0, 0, 3]},
          {text: `${t('area_over_limit')}: ${data.pixelsOverLimit && data.areaOverLimit < .005 ? '<0.01' : data.areaOverLimit.toFixed(2)}%`, fontSize: 10, bold: true, margin: [0, 0, 0, 4]},
          ...(!data.pixelsOverLimit ? [{text: formatText('no_areas_over_limit', {limit: data.limit}), fontSize: 9, color: '#666', margin: [0, 0, 0, 4]}] : []),
          field(t('included_inks') + ': ' + data.includedChannels.join(', '), {fontSize: 8, color: '#666', margin: [0, 0, 0, 3]}),
          ...(data.excludedChannels.length ? [field(t('excluded_inks') + ': ' + data.excludedChannels.join(', '), {fontSize: 8, color: '#666'})] : [])
        ]}], margin: [0, 0, 0, 18]});
    }
    const info = report.colorInfo || {};
    const notes = report.average ? [t('report_average_note')] : [];
    if (info.rgbStatus === 'detected') notes.push(t('report_rgb_note'));
    if (info.rgbStatus === 'unknown') notes.push(t('report_inspection_note'));
    if (report.heat) notes.push(t('report_limit_note'));
    sections.push({text: notes.join(' '), fontSize: 8, color: '#777', lineHeight: report.lang === 'zh' ? 1.05 : 1.25});
    const profiles = colorSummary(report.colorInfo).filter(line => !line.startsWith(t('rgb_warning_title') + ':') && !line.startsWith(t('color_unknown_title') + ':'));
    for (const line of profiles) sections.push(field(line, {fontSize: 8, color: '#888', margin: [0, 6, 0, 0]}));
    const doc = {pageSize: 'A4', pageMargins: [40, 40, 40, 60], defaultStyle: {font: 'Roboto', fontSize: 10, color: '#1a1a1a', lineHeight: report.lang === 'zh' ? .84 : 1},
      info: {title: t('report_title'), author: 'inkcoverage.app', subject: t('report_subject')}, content: sections,
      footer: (page, pages) => ({stack: [{text: 'Report created by inkcoverage.app', link: 'https://inkcoverage.app', alignment: 'center', fontSize: 8, color: '#999'},
        ...(pages > 1 ? [{text: `${page} / ${pages}`, alignment: 'center', fontSize: 7, color: '#bbb', margin: [0, 3, 0, 0]}] : [])], margin: [40, 20, 40, 0]})};
    function prepareText(node) {
      if (Array.isArray(node)) { node.forEach(prepareText); return; }
      if (!node || typeof node !== 'object') return;
      if (typeof node.text === 'string') {
        const converted = text(node.text); node.text = converted.text;
        node.wordBreak ??= converted.wordBreak; customText.push(node);
      }
      for (const [key, value] of Object.entries(node)) if (key !== 'text') prepareText(value);
    }
    prepareText(doc.content);
    return {doc, customText};
  }
  async function download() {
    if (busy || !available()) return;
    let report;
    try { report = snapshot(); } catch (error) { setActionError('export', error.message); return; }
    busy = true; setActionError('export'); sync();
    try {
      await tools();
      // Render sequentially to bound transient canvas memory on large batches.
      let doc; const customText = [];
      for (const page of report.pages) {
        const source = {...report, ...page};
        const preview = await thumbnail(source, source.geometry);
        const heatPreview = source.heat ? await thumbnail(source, source.heat.scope.geometry, source.heat.data.image) : null;
        const part = definition(source, preview, heatPreview); customText.push(...part.customText);
        if (!doc) {
          doc = part.doc;
          const displayPages = pages => pageGroups(pages).map(([first, last]) => first === last ? String(first) : `${first}–${last}`).join(', ');
          const summary = [`${t('report_pages')}: ${displayPages(report.pages.map(page => page.scope.page))}`, ...report.summary];
          const checked = report.pages.filter(page => page.heat).map(page => page.scope.page);
          if (checked.length) summary.push(`${t('report_limit_pages')}: ${displayPages(checked)}`);
          const nodes = summary.map(line => text(line, {fontSize: 8, color: '#666', margin: [0, 0, 0, 3]}));
          customText.push(...nodes); doc.content.splice(3, 0, {stack: nodes, margin: [0, 0, 0, 14]});
        } else {
          part.doc.content[0].pageBreak = 'before'; doc.content.push(...part.doc.content);
        }
      }
      await fonts(customText);
      if (!stillCurrent(report)) return;
      const blob = await pdfMake.createPdf(doc).getBlob();
      if (!stillCurrent(report)) return;
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = report.filename;
      document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
      announceStatus(t('report_downloaded'));
    } catch (error) {
      if (stillCurrent(report)) setActionError('export', t('report_failed'));
    } finally { busy = false; sync(); }
  }
  button.addEventListener('click', download); window.coverageReport = {sync, retainPreview}; sync();
})();
