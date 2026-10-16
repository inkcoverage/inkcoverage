(() => {
  const dialog = document.getElementById('batchDialog');
  const form = document.getElementById('batchDialogForm');
  const from = document.getElementById('batchRangeFrom');
  const to = document.getElementById('batchRangeTo');
  const error = document.getElementById('batchDialogError');
  const start = document.getElementById('btnStartBatch');
  let scope = null;
  const trigger = () => btnAnalyze;
  const rangeMode = () => form.elements.batchPagesMode.value === 'range';
  const available = () => state.pdfId && state.previewReady && !state.isLoading && !state.isAnalyzing
    && !state.sourceUnavailable && !sourceTimeExpired() && !isDragging && !isResizing && !window.channelPreview?.busy();
  function range() {
    if (form.elements.batchPagesMode.value === 'current') return [state.currentPage, state.currentPage];
    if (!rangeMode()) return [1, state.pageCount];
    const a = Number(from.value), b = Number(to.value);
    return from.checkValidity() && to.checkValidity() && Number.isInteger(a) && Number.isInteger(b)
      && a >= 1 && b >= a && b <= state.pageCount ? [a, b] : null;
  }
  function sync() {
    btnAnalyzeAll.hidden = true;
    document.getElementById('batchCurrentPageChoice').hidden = false;
    btnAnalyze.setAttribute('aria-haspopup', 'dialog');
    btnAnalyze.setAttribute('aria-controls', 'batchDialog');
    btnAnalyze.setAttribute('aria-expanded', String(dialog.open));
    if (!dialog.open) return;
    if (!scope || !sameView(scope)) { close(); return; }
    document.getElementById('batchRangeFields').hidden = !rangeMode();
    const bounds = range();
    document.getElementById('batchDialogCount').textContent = bounds
      ? formatText('batch_pages_selected', {count: bounds[1] - bounds[0] + 1}) : '';
    document.getElementById('batchDialogScope').textContent = t(state.crop ? 'batch_selected_scope' : 'batch_full_scope');
    start.disabled = !available();
    if (!available()) {
      error.hidden = false;
      error.textContent = t('file_expired_short');
    }
  }
  function open() {
    if (!available() || dialog.open) return;
    scope = captureScope();
    form.elements.batchPagesMode.value = 'current';
    from.value = '1'; to.value = String(state.pageCount);
    from.max = to.max = String(state.pageCount);
    error.hidden = true; error.textContent = '';
    dialog.showModal();
    sync();
    form.querySelector('input:checked').focus();
  }
  function close() { if (dialog.open) dialog.close(); }
  dialog.addEventListener('close', () => {
    scope = null;
    sync();
    const target = state.pdfId && !trigger().disabled ? trigger() : state.pdfId ? canvasArea : document.getElementById('btnWelcomeUpload');
    target.focus({preventScroll: true});
  });
  document.getElementById('btnCloseBatchDialog').addEventListener('click', close);
  document.getElementById('btnCancelBatchDialog').addEventListener('click', close);
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const controls = [...dialog.querySelectorAll('button, input, [tabindex]')]
      .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length
        && !node.closest('[hidden]') && (node.type !== 'radio' || node.checked));
    const first = controls[0], last = controls.at(-1);
    if (!controls.includes(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
      event.preventDefault(); (event.shiftKey ? last : first)?.focus();
    }
  });
  form.addEventListener('input', () => { error.hidden = true; sync(); });
  form.addEventListener('change', () => { error.hidden = true; sync(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!scope || !sameView(scope) || !available()) { sync(); return; }
    const bounds = range();
    if (!bounds) {
      error.hidden = false;
      error.textContent = formatText('batch_range_invalid', {count: state.pageCount});
      (from.checkValidity() && Number(from.value) <= Number(to.value) ? to : from).focus();
      return;
    }
    const isRange = rangeMode();
    const currentPage = form.elements.batchPagesMode.value === 'current';
    const pages = Array.from({length: bounds[1] - bounds[0] + 1}, (_, index) => bounds[0] + index);
    close();
    if (currentPage) analyzeCurrentPage();
    else runBatch(isRange ? 'range' : 'all', isRange ? pages : null);
  });
  btnAnalyzeAll.setAttribute('aria-haspopup', 'dialog');
  btnAnalyzeAll.setAttribute('aria-controls', 'batchDialog');
  btnAnalyzeAll.setAttribute('aria-expanded', 'false');
  window.batchPicker = {open, close, sync};
  sync();
})();
