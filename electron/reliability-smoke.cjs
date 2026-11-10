'use strict';
const fs = require('node:fs'), path = require('node:path');
const { execFile } = require('node:child_process');
const { app, dialog } = require('electron');
const { DownloadQueue } = require('./model.cjs');
const { makeRecovery } = require('./task-recovery.cjs');
const { windowsAcl, windowsUserSid } = require('./temp-credentials.cjs');

async function run(collector, check, until, wait, profile) {
  const queue = collector.queue, source = collector.tabs.active, settings = { ...collector.settings };
  const windowSize = collector.window.getSize(), visible = collector.window.isVisible();
  const realWrite = collector.persistence.write;
  const realFolderPicker = dialog.showOpenDialog;
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const output = path.join(profile, 'reliability-output');
  fs.mkdirSync(output, { recursive: true });
  const testQueue = new DownloadQueue(() => ({ promise: Promise.resolve({ folder: output }), cancel() {} }), () => collector.publish());
  const directory = collector.credentials.directory;
  try {
    check('credential protection initialized without warnings', collector.credentials.ready && !collector.shared.credentialError);
    const cookie = collector.credentials.write('controlled-fake-credential');
    if (process.platform === 'win32') {
      const allowed = [windowsUserSid(), 'S-1-5-18', 'S-1-5-32-544'];
      const directoryAcl = windowsAcl(directory), fileAcl = windowsAcl(cookie);
      check('Windows directory and fresh cookie file exclude unexpected allowed identities',
        directoryAcl.protected && [directoryAcl, fileAcl].every(acl =>
          !acl.rules.some(rule => rule.allow && !allowed.includes(rule.sid))));
    }
    collector.credentials.remove(cookie);
    let secondarySeen = false;
    const secondary = () => { secondarySeen = true; };
    app.on('second-instance', secondary);
    try {
      const env = { ...process.env, YT_COLLECTOR_HOME: profile }; delete env.ELECTRON_RUN_AS_NODE;
      await new Promise((resolve, reject) => execFile(process.execPath,
        [path.resolve(__dirname, '..'), '--collector-persistence-check'],
        { env, windowsHide: true, timeout: 20000 }, error => error ? reject(error) : resolve()));
      check('a real secondary process exits before initialization and signals the existing window', secondarySeen);
    } finally { app.removeListener('second-instance', secondary); }

    collector.queue = testQueue; collector.settings.autoParse = false; collector.settings.panelWidth = 390;
    const url = 'https://www.xiaohongshu.com/explore/6a8fbc2400000000070063fc';
    const tab = collector.openTab(url, { reuse: true });
    await until(() => !tab.state.loading, 'reliability fixture document');
    const item = { id: 'reliability-note', kind: 'images', url, title: 'Controlled image collection', author: 'Fixture',
      images: Array.from({ length: 30 }, (_, i) => ({ index: i + 1,
        url: `https://sns-webpic-qc.xhscdn.com/image-${i + 1}`, preview_url: 'https://sns-webpic-qc.xhscdn.com/image-a',
        width: 1600, height: 1200 })) };
    const result = { site: 'xiaohongshu', items: [item] }, receipt = 'reliability-receipt';
    collector.cache.set(receipt, { url, result, context: null });
    Object.assign(tab.state, { result, inspectionId: receipt, parsing: false }); collector.publish();
    await ui('document.getElementById("resources-tab").click()');
    await until(() => ui('document.querySelectorAll("[data-image]").length===30'), 'long image list rendered');
    const footerScript = `(() => {
      const footer = document.getElementById('resource-actions').getBoundingClientRect();
      const button = document.getElementById('download').getBoundingClientRect();
      const panel = document.getElementById('resources-panel').getBoundingClientRect();
      const aside = document.querySelector('aside').getBoundingClientRect();
      return !document.getElementById('resource-actions').hidden && panel.bottom <= footer.top + 1 &&
        button.top >= footer.top && button.bottom <= aside.bottom && button.right <= aside.right;
    })()`;
    for (const size of [[1440, 920], [1060, 700]]) {
      collector.window.setSize(...size); collector.publish(); await wait(150);
      check(`fixed batch footer fits at ${size.join('x')}`, await ui(footerScript));
      const top = await ui('document.getElementById("download").getBoundingClientRect().top');
      await ui('document.getElementById("resources-panel").scrollTop=100000');
      check('scrolling a long image list does not move the batch action', await ui('document.getElementById("download").getBoundingClientRect().top') === top);
    }
    await ui('document.getElementById("select-all").click()');
    check('deselecting all images disables the fixed action and shows zero count',
      await ui('document.getElementById("download").disabled&&document.getElementById("download").textContent.includes("0")'));
    await ui('document.getElementById("select-all").click()');
    check('selecting all restores the correct batch count',
      await ui('!document.getElementById("download").disabled&&document.getElementById("download").textContent.includes("30")'));
    await collector.window.webContents.executeJavaScript('document.getElementById("resources-panel").scrollTop=0');
    collector.window.show(); await wait(100);
    fs.writeFileSync(path.join(profile, '..', 'reliability-images.png'), (await collector.window.webContents.capturePage()).toPNG());
    await ui('document.getElementById("compact-images").click()');
    for (const size of [[1440, 920], [1060, 700]]) {
      collector.window.setSize(...size); collector.publish(); await wait(150);
      check(`compact thumbnails keep two columns and single-image controls inside cards at ${size.join('x')}`,
        await ui(`(() => {
          const cards=[...document.querySelectorAll('.image-card')], first=cards[0].getBoundingClientRect(), second=cards[1].getBoundingClientRect();
          return first.top===second.top && second.left>=first.right && cards.every(card => {
            const area=card.getBoundingClientRect();
            return [...card.querySelectorAll('.image-meta>span,.image-download')].every(element => {
              const r=element.getBoundingClientRect();
              return r.left>=area.left && r.right<=area.right && r.top>=area.top && r.bottom<=area.bottom;
            });
          });
        })()`) && await ui(footerScript));
    }
    check('compact layout keeps image selections and shows all single-image downloads',
      await ui('document.querySelectorAll("[data-image]:checked").length===30&&document.querySelectorAll("[data-download-image]").length===30'));
    fs.writeFileSync(path.join(profile, '..', 'reliability-images-compact.png'), (await collector.window.webContents.capturePage()).toPNG());
    await ui('document.getElementById("compact-images").click()');

    await ui('document.getElementById("settings-tab").click()');
    await until(() => ui('document.getElementById("proxy").value===' + JSON.stringify(settings.proxy)), 'settings draft ready');
    await ui('document.getElementById("proxy").value="http://127.0.0.1:7890";document.getElementById("proxy").dispatchEvent(new Event("input",{bubbles:true}))');
    collector.publish(); await wait(50);
    check('editing settings marks an unsaved draft without applying it',
      collector.settings.proxy === settings.proxy && await ui('document.getElementById("proxy").value==="http://127.0.0.1:7890"&&document.getElementById("settings-state").textContent.includes("未保存")'));
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [output] });
    await ui('document.getElementById("choose-folder").click()');
    await until(() => ui('document.getElementById("save-dir").value===' + JSON.stringify(output)), 'folder draft ready');
    check('choosing a folder changes only the unsaved draft', collector.settings.saveDir === settings.saveDir);
    dialog.showOpenDialog = realFolderPicker;
    // Save the real existing proxy rather than contacting the controlled draft address.
    await ui('document.getElementById("proxy").value=' + JSON.stringify(settings.proxy) +
      ';document.getElementById("save-dir").value=' + JSON.stringify(output) + ';document.getElementById("save-settings").click()');
    await until(() => collector.settings.saveDir === output && ui('document.getElementById("settings-state").textContent==="已保存"'), 'explicit settings save');
    check('one explicit save persists folder and other settings together',
      JSON.parse(fs.readFileSync(path.join(profile, 'settings.json'), 'utf8')).saveDir === output);

    const recovery = makeRecovery({ url }, item, { indices: [2] }, { ...collector.settings, saveDir: output });
    const failed = { id: 'recovery-from-history', status: 'failed', title: 'Interrupted fixture', logs: ['first line'], recovery };
    testQueue.tasks.push(failed); collector.publish();
    await ui('document.getElementById("tasks-tab").click()');
    await until(() => ui('!!document.querySelector("[data-task-id=recovery-from-history]")'), 'recovered task card');
    check('history-only recovery offers retry and displays original output and source',
      await ui('!document.querySelector("[data-retry=recovery-from-history]").hidden&&document.querySelector("[data-task-id=recovery-from-history] [data-source]").textContent.includes("xiaohongshu")&&document.querySelector("[data-task-id=recovery-from-history] [data-directory]").textContent.includes("reliability-output")'));
    await ui(`(() => {
      const card=document.querySelector('[data-task-id=recovery-from-history]');
      window.reliabilityCard=card; window.reliabilityLog=card.querySelector('pre').firstChild;
      card.querySelector('details').open=true;
      const selection=getSelection(), range=document.createRange();
      range.setStart(window.reliabilityLog,0);range.setEnd(window.reliabilityLog,5);
      selection.removeAllRanges();selection.addRange(range);
      card.querySelector('[data-retry]').focus();
    })()`);
    failed.logs.push('second line'); failed.percent = 37; collector.publish(); await wait(100);
    check('task updates preserve card nodes, opened logs, selected text and focus',
      await ui('window.reliabilityCard===document.querySelector("[data-task-id=recovery-from-history]")&&window.reliabilityLog===window.reliabilityCard.querySelector("pre").firstChild&&getSelection().toString()==="first"&&document.activeElement.dataset.retry==="recovery-from-history"&&window.reliabilityCard.querySelector("details").open'));
    fs.writeFileSync(path.join(profile, '..', 'reliability-tasks.png'), (await collector.window.webContents.capturePage()).toPNG());
    const originalInspect = collector.inspect;
    let freshContext;
    collector.inspect = async current => {
      freshContext = { generation: 'fresh', public: true };
      const newReceipt = 'reliability-fresh';
      collector.invalidate(current);
      const freshResult = { site: 'xiaohongshu', items: [{ ...item, images: item.images.map(image => ({ ...image, url: image.url + '?signature=fresh' })) }] };
      collector.cache.set(newReceipt, { url, result: freshResult, context: freshContext });
      Object.assign(current.state, { result: freshResult, inspectionId: newReceipt }); collector.publish();
    };
    try {
      const newId = await collector.retryTask(failed.id);
      const retried = testQueue.tasks.find(task => task.id === newId);
      check('restart retry reparses and sends fresh context and original selection to normal queue',
        retried.spec.context === freshContext && retried.spec.indices.join(',') === '2' && retried.spec.settings.saveDir === output);
    } finally { collector.inspect = originalInspect; }
    await until(() => testQueue.active.size === 0, 'recovered fixture job completes');

    collector.persistence.write = file => { throw Object.assign(new Error('controlled disk full'), { code: 'ENOSPC' }); };
    collector.settings.saveDir = profile;
    check('simulated disk failure is caught and exposed without stopping the UI', collector.persist() === false);
    await until(() => ui('!document.getElementById("system-warning").hidden'), 'save error banner');
    collector.persistence.write = realWrite;
    await collector.invoke('retry-save');
    await until(() => ui('document.getElementById("system-warning").hidden'), 'save warning cleared after retry');
    check('retry save clears the failure banner after the disk recovers', !collector.shared.storageError);
  } finally {
    collector.persistence.write = realWrite;
    dialog.showOpenDialog = realFolderPicker;
    testQueue.close(); await wait(50); collector.queue = queue; collector.settings = settings;
    collector.window.setSize(...windowSize);
    if (!source.closed) collector.activateTab(source.id);
    collector.publish(); collector.persist(); if (!visible) collector.window.hide();
  }
}
module.exports = { run };
