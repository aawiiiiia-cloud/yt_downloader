'use strict';
function acquireInstance(app, currentWindow) {
  if (!app.requestSingleInstanceLock()) { app.quit(); return false; }
  app.on('second-instance', () => {
    const window = currentWindow();
    if (!window || window.isDestroyed()) return;
    if (window.isMinimized()) window.restore();
    window.show(); window.focus();
  });
  return true;
}
module.exports = { acquireInstance };
