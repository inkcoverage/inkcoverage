// Approved, illustrative welcome tour. Does not change measurements or upload state.
(() => {
  const paths = {
    coverage: '<path d="M4 20V11h4v9m4 0V4h4v16m4 0v-7h4v7M2 23h24"/>',
    heat: '<path d="M14 2c2 5-3 7-1 10 1-2 3-3 4-4 4 4 6 9 3 13-3 4-9 4-12 1C3 17 8 9 14 2Z"/>',
    area: '<path d="M3 9V3h6m10 0h6v6M3 19v6h6m16-6v6h-6M7 7h14v14H7Z"/>',
    report: '<path d="M6 2h11l5 5v17H6V2Zm11 0v6h5M10 13h8m-8 5h8"/>'
  };
  function icon(name) { return `<svg viewBox="0 0 28 28" aria-hidden="true">${paths[name]}</svg>`; }
  function label(mode = 'document', id = 'main') {
    const only = mode === 'channel', heat = mode === 'heatmap';
    return `<svg viewBox="0 0 260 310" role="img" aria-label="${t(only ? 'tour_channel_aria' : heat ? 'tour_overlay_aria' : 'tour_artwork_aria')}">
      <defs><linearGradient id="label-${id}" x2="1" y2="1"><stop stop-color="#e7eff7"/><stop offset="1" stop-color="#f9faf8"/></linearGradient></defs>
      <path d="M18 27h19m-9-9v19M224 27h19m-9-9v19M18 274h19m-9-9v19M224 274h19m-9-9v19" stroke="#7e96be" stroke-width=".8" fill="none"/>
      <rect x="39" y="22" width="185" height="259" rx="4" fill="${only ? '#f4f6fa' : `url(#label-${id})`}"/>
      <rect x="49" y="33" width="165" height="237" rx="1" fill="none" stroke="#7c92b8" stroke-dasharray="3 3" stroke-width=".6"/>
      <g fill="${only ? '#152e61' : '#182c48'}" font-family="DM Sans,sans-serif">
        <text x="64" y="62" font-size="8" letter-spacing="2">BOTANICAL SERIES / 01</text>
        <text x="63" y="107" font-size="31" font-weight="700" letter-spacing="-1">FIELD</text>
        <text x="63" y="139" font-size="31" font-weight="700" letter-spacing="-1">NOTES</text>
        <text x="64" y="241" font-size="7" letter-spacing="1.7">ILLUSTRATIVE LABEL</text>
        ${only ? '' : '<text x="64" y="254" font-size="6.5" letter-spacing=".8">INK / PAPER / POSSIBILITY</text>'}
      </g>
      <g stroke="${only ? '#152e61' : '#577799'}" fill="none" stroke-width=".8">
        <path d="M65 157h135M64 190c26-36 57-4 84-25s47-8 55-12M64 200c29-26 45-5 75-18s45-14 64-10M64 210c18-19 37-9 58-14s53-18 81-9M64 220c20-14 40-13 62-14s60-12 77-8"/>
      </g>
      ${heat ? '<path d="M63 85h103v59H63Z" fill="#ff8a30" opacity=".83"/><path d="M67 92h91v47H67Z" fill="#ee4548" opacity=".82"/>' : ''}
      ${only || heat ? '' : '<rect x="49" y="33" width="165" height="237" fill="none" stroke="#557eee" stroke-width="1.1"/><path d="M49 32v-7m165 7v-7M49 27h165" stroke="#89a7ed" stroke-width=".7"/><rect x="81" y="17" width="96" height="16" rx="3" fill="#172744"/><text x="129" y="28" text-anchor="middle" fill="#d4e3ff" font-size="6.5" font-family="JetBrains Mono,monospace">' + t('selected_area') + '</text>'}
    </svg>`;
  }

  let mode = 'document';
  function sync() {
    const root = document.getElementById('emptyState');
    root.querySelector('#mockArt').innerHTML = label(mode);
    const rows = root.querySelector('#mockChannelReadout');
    rows.hidden = mode === 'heatmap';
    for (const [index, row] of [...rows.children].entries()) row.hidden = mode === 'channel' && index !== 4;
    root.querySelector('#mockReadoutTitle').textContent = t(mode === 'heatmap' ? 'tour_overlay_title' : mode === 'channel' ? 'tour_channel_title' : 'tour_average');
    root.querySelector('#mockModeNote').textContent = t(mode === 'heatmap' ? 'tour_overlay_note' : mode === 'channel' ? 'tour_channel_note' : 'tour_note');
    root.querySelector('.mock-upload-facts').textContent = formatText('tour_upload_facts', {max:serverLimits?.maxUploadMb ?? 50, pages:serverLimits?.maxDocumentPages ?? 100});
    for (const control of root.querySelectorAll('[data-art-mode]')) control.setAttribute('aria-pressed', String(control.dataset.artMode === mode));
  }
  document.getElementById('mockStart').addEventListener('click', () => document.getElementById('btnWelcomeUpload').click());
  for (const control of document.querySelectorAll('[data-art-mode]')) control.addEventListener('click', () => {mode = control.dataset.artMode; sync();});
  window.welcomeTour = {sync}; sync();
})();
