'use strict';
(() => {
  const workspace = document.getElementById('workspace'), divider = document.getElementById('pane-divider');
  let preferred = 390, drag = null, saving = 0, saveVersion = 0;
  const limits = () => {
    const style = getComputedStyle(workspace);
    const right = workspace.getBoundingClientRect().right - parseFloat(style.paddingRight);
    const available = workspace.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - divider.offsetWidth;
    return { min: 330, max: Math.max(330, available - 400), right };
  };
  function apply(width = preferred) {
    const { min, max } = limits();
    const value = Math.round(Math.min(max, Math.max(min, Number(width) || 390)));
    workspace.style.setProperty('--panel-width', value + 'px');
    divider.setAttribute('aria-valuemin', String(min)); divider.setAttribute('aria-valuemax', String(max));
    divider.setAttribute('aria-valuenow', String(value)); divider.setAttribute('aria-valuetext', `解析区域 ${value} 像素`);
    return value;
  }
  async function save() {
    const ticket = ++saveVersion; saving++;
    try {
      const width = await window.collector.call('panel-width', { width: preferred });
      if (ticket === saveVersion) preferred = width;
    } catch {} // The current layout remains usable if the app is closing.
    finally { saving--; if (ticket === saveVersion) apply(); }
  }
  function move(x) {
    if (!drag || !Number.isFinite(x)) return;
    preferred = apply(limits().right - x - (divider.offsetWidth - drag.offset));
  }
  function finish(commit = true) {
    if (!drag) return;
    const previous = drag; drag = null;
    document.body.classList.remove('resizing-panes');
    if (divider.hasPointerCapture(previous.id)) divider.releasePointerCapture(previous.id);
    window.collector.call('resize-end').catch(() => {});
    if (commit) save();
    else { preferred = previous.width; apply(); }
  }
  divider.addEventListener('pointerdown', event => {
    if (event.button !== 0 || drag) return;
    event.preventDefault(); divider.focus();
    drag = { id: event.pointerId, offset: event.clientX - divider.getBoundingClientRect().left, width: preferred };
    divider.setPointerCapture(event.pointerId); document.body.classList.add('resizing-panes');
    window.collector.call('resize-start').catch(() => finish(false));
  });
  divider.addEventListener('pointermove', event => { if (event.pointerId === drag?.id) move(event.clientX); });
  divider.addEventListener('pointerup', event => { if (event.pointerId === drag?.id) { move(event.clientX); finish(); } });
  divider.addEventListener('pointercancel', () => finish());
  divider.addEventListener('lostpointercapture', () => finish());
  divider.addEventListener('dblclick', () => { finish(); preferred = apply(390); save(); });
  divider.addEventListener('keydown', event => {
    const steps = { ArrowLeft: 20, ArrowRight: -20, Home: -Infinity, End: Infinity };
    if (event.key === 'Escape' && drag) { event.preventDefault(); finish(false); return; }
    if (!(event.key in steps) || drag) return;
    event.preventDefault();
    const { min, max } = limits();
    preferred = apply(event.key === 'Home' ? min : event.key === 'End' ? max : preferred + steps[event.key]); save();
  });
  window.addEventListener('blur', () => finish());
  window.addEventListener('resize', () => apply());
  window.collector.onResizePointer(pointer => {
    if (!drag) return;
    if (Number.isFinite(pointer.x)) move(pointer.x);
    if (pointer.type === 'end') finish(!pointer.cancel);
  });
  window.collectorLayout = {
    load(width) { if (!drag && !saving) { preferred = Number.isFinite(width) ? width : 390; apply(); } },
  };
  apply();
})();
