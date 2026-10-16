// Zoom changes display geometry only. PDF crop coordinates and result ownership
// stay in the analyzer's normalized page coordinates.
(() => {
  const tools = document.getElementById('previewTools');
  const stage = document.getElementById('previewStage');
  const hand = document.getElementById('btnPanPreview');
  const hint = document.getElementById('previewMoveHint');
  const buttons = [...tools.querySelectorAll('[data-zoom]')];
  let zoom = 1, handMode = false, spaceHeld = false, pan = null;
  let readyBefore = false, frame = null, hovered = false;
  let keyboardNavigation = false;
  const moving = () => handMode || spaceHeld || Boolean(pan);
  const ready = () => state.previewReady && !state.isLoading;

  function finishPan() {
    const active = pan;
    pan = null;
    if (active && canvasArea.hasPointerCapture(active.id)) canvasArea.releasePointerCapture(active.id);
    render();
  }
  function reset() {
    zoom = 1; handMode = false; spaceHeld = false;
    finishPan();
    canvasArea.scrollTo(0, 0);
    previewContainer.style.removeProperty('width');
    previewContainer.style.removeProperty('height');
    stage.style.removeProperty('width');
    stage.style.removeProperty('height');
    readyBefore = false;
    schedule();
  }
  function viewCenter() {
    const image = previewContainer.getBoundingClientRect();
    const viewport = canvasArea.getBoundingClientRect();
    if (!image.width || !image.height) return {x: .5, y: .5};
    return {
      x: Math.max(0, Math.min(1, (viewport.left + canvasArea.clientWidth / 2 - image.left) / image.width)),
      y: Math.max(0, Math.min(1, (viewport.top + canvasArea.clientHeight / 2 - image.top) / image.height)),
    };
  }
  function layout(center = viewCenter()) {
    if (!ready() || !previewImg.naturalWidth || !previewImg.naturalHeight) return;
    const mobile = matchMedia('(max-width: 900px)').matches;
    // Desktop flex layout reserves room for the footer, warning and demo
    // caption. Mobile keeps its stacked, page-scrolling preview.
    if (mobile) canvasArea.style.height = `${Math.max(180, Math.min(520, innerHeight * .55))}px`;
    else canvasArea.style.removeProperty('height');
    const height = canvasArea.getBoundingClientRect().height;
    // Reserve padding and scrollbar space consistently so Fit does not change
    // scale when an overflow scrollbar appears at a higher zoom level.
    const availableWidth = Math.max(1, canvasArea.getBoundingClientRect().width - 50);
    const availableHeight = Math.max(1, height - 50);
    const scale = Math.min(availableWidth / previewImg.naturalWidth, availableHeight / previewImg.naturalHeight, 1);
    const width = previewImg.naturalWidth * scale * zoom;
    const imageHeight = previewImg.naturalHeight * scale * zoom;
    previewContainer.style.width = `${width}px`;
    previewContainer.style.height = `${imageHeight}px`;
    stage.style.width = `${width + 32}px`;
    stage.style.height = `${imageHeight + 32}px`;
    if (zoom === 1) canvasArea.scrollTo(0, 0);
    else canvasArea.scrollTo(previewContainer.offsetLeft + center.x * width - canvasArea.clientWidth / 2,
      previewContainer.offsetTop + center.y * imageHeight - canvasArea.clientHeight / 2);
  }
  function schedule() {
    if (frame !== null) return;
    frame = requestAnimationFrame(() => { frame = null; layout(); });
  }
  function render() {
    const enabled = ready();
    buttons.forEach(button => {
      button.disabled = !enabled || isDragging || isResizing || Boolean(pan);
      button.setAttribute('aria-pressed', String(Number(button.dataset.zoom) === zoom));
    });
    hand.disabled = !enabled || isDragging || isResizing || Boolean(pan);
    hand.setAttribute('aria-pressed', String(handMode));
    canvasArea.dataset.moving = String(moving());
    canvasArea.dataset.panning = String(Boolean(pan));
    const touch = matchMedia('(pointer: coarse)').matches;
    hint.textContent = t(handMode ? 'pan_active_hint' : touch ? 'pan_touch_hint' : 'pan_hint');
    if (enabled && !readyBefore) schedule();
    readyBefore = enabled;
  }
  buttons.forEach(button => button.addEventListener('click', () => {
    if (!ready() || isDragging || isResizing || pan) return;
    const center = viewCenter();
    zoom = Number(button.dataset.zoom);
    layout(center); render();
  }));
  hand.addEventListener('click', () => {
    if (!ready() || isDragging || isResizing || pan) return;
    handMode = !handMode;
    render();
  });
  canvasArea.addEventListener('pointerenter', () => { hovered = true; });
  canvasArea.addEventListener('pointerleave', () => { hovered = false; });
  document.addEventListener('pointerdown', () => { keyboardNavigation = false; }, true);
  canvasArea.addEventListener('pointerdown', event => {
    if (!ready() || !event.isPrimary || event.button !== 0 || isDragging || isResizing) return;
    if (event.target.closest('button, a, input')) return;
    canvasArea.focus({preventScroll: true});
    if (!moving()) return;
    event.preventDefault(); event.stopImmediatePropagation();
    pan = {id: event.pointerId, x: event.clientX, y: event.clientY,
      left: canvasArea.scrollLeft, top: canvasArea.scrollTop};
    canvasArea.setPointerCapture(event.pointerId);
    render();
  }, true);
  canvasArea.addEventListener('pointermove', event => {
    if (event.pointerType === 'mouse') keyboardNavigation = false;
    if (!pan || pan.id !== event.pointerId) return;
    event.preventDefault();
    canvasArea.scrollTo(pan.left + pan.x - event.clientX, pan.top + pan.y - event.clientY);
  });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => canvasArea.addEventListener(type, event => {
    if (pan?.id === event.pointerId) finishPan();
  }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Tab') keyboardNavigation = true;
    if (event.code !== 'Space' || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || !ready()) return;
    if (!helpModal.hidden || document.querySelector('dialog[open]') || isDragging || isResizing || event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
    // A mouse-clicked button keeps focus when the pointer returns to the PDF.
    // In that case Space pans; Tab navigation still gives buttons normal Space
    // activation, and editable fields always retain ordinary typing behavior.
    if (event.target.closest('button, a, summary') && (!hovered || keyboardNavigation)) return;
    if (!hovered && !canvasArea.contains(document.activeElement)) return;
    event.preventDefault(); spaceHeld = true; render();
  });
  document.addEventListener('keyup', event => {
    if (event.code !== 'Space' || !spaceHeld) return;
    event.preventDefault(); spaceHeld = false; finishPan();
  });
  function cancelGestures() { spaceHeld = false; finishPan(); handleEnd(); }
  window.addEventListener('blur', cancelGestures);
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancelGestures(); });
  previewImg.addEventListener('load', schedule);
  const observer = new ResizeObserver(schedule);
  observer.observe(canvasArea);
  observer.observe(tools);
  observer.observe(document.getElementById('rgbWarning'));
  observer.observe(document.getElementById('demoCaption'));
  window.addEventListener('resize', () => { cancelGestures(); schedule(); });
  window.previewZoom = {reset, render, isMoving: moving};
  render(); schedule();
})();
