'use strict';
(function(root) {
  // A receipt is visual feedback only: the caller must confirm queue acceptance first.
  function createDownloadFeedback(host) {
    const flights = new Set();
    const pulses = new Map();
    const reduced = () => host.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    function pulse(target) {
      if (!target?.isConnected) return;
      host.clearTimeout(pulses.get(target));
      target.classList.remove('download-arrived');
      void target.offsetWidth;
      target.classList.add('download-arrived');
      pulses.set(target, host.setTimeout(() => {
        target.classList.remove('download-arrived'); pulses.delete(target);
      }, 700));
    }
    function clear() {
      for (const stop of [...flights]) stop();
      for (const [target, timer] of pulses) {
        host.clearTimeout(timer); target.classList.remove('download-arrived');
      }
      pulses.clear();
    }
    function fly(origin, target) {
      if (!target?.isConnected) return Promise.resolve();
      if (!origin || reduced() || !host.document.body.animate) {
        pulse(target); return Promise.resolve();
      }
      while (flights.size >= 3) flights.values().next().value();
      return new Promise(resolve => {
        const icon = host.document.createElement('span');
        icon.className = 'download-flight'; icon.setAttribute('aria-hidden', 'true');
        host.document.body.appendChild(icon);
        let animation, timer, stopped = false;
        const stop = () => {
          if (stopped) return;
          stopped = true; host.clearTimeout(timer); animation?.cancel();
          icon.remove(); flights.delete(stop);
          host.removeEventListener('resize', stop); resolve();
        };
        flights.add(stop); host.addEventListener('resize', stop, { once: true });
        timer = host.setTimeout(stop, 1600);
        try {
          const rect = target.getBoundingClientRect();
          if (!rect.width || !rect.height) { stop(); return; }
          const clampX = x => Math.max(17, Math.min(host.innerWidth - 17, x));
          const clampY = y => Math.max(17, Math.min(host.innerHeight - 17, y));
          const x = clampX(origin.right - 16), y = clampY(origin.top + origin.height / 2);
          const tx = clampX(rect.left + rect.width / 2), ty = clampY(rect.top + rect.height / 2);
          const transform = (px, py, scale) => `translate(${px - 16}px, ${py - 16}px) scale(${scale})`;
          animation = icon.animate([
            { transform: transform(x, y, .4), opacity: 0, offset: 0 },
            { transform: transform(clampX(x + 12), clampY(y - 24), 1.15), opacity: 1, offset: .2 },
            { transform: transform(clampX(x + 18), clampY(y - 34), 1), opacity: 1, offset: .3 },
            { transform: transform(clampX((x + tx) / 2 + 24), clampY((y + ty) / 2 - 32), .85), opacity: 1, offset: .65 },
            { transform: transform(tx, ty, .3), opacity: .1, offset: 1 },
          ], { duration: 780, easing: 'cubic-bezier(.3,.05,.45,1)', fill: 'forwards' });
          animation.finished.then(() => { if (!stopped) pulse(target); stop(); }, stop);
        } catch { pulse(target); stop(); }
      });
    }
    host.addEventListener('pagehide', clear);
    return { fly, clear };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createDownloadFeedback };
  else root.collectorDownloadFeedback = createDownloadFeedback(root);
})(typeof window !== 'undefined' ? window : globalThis);
