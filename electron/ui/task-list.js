(function(root) {
  'use strict';
  const renderers = new WeakMap();
  const labels = { queued: '\u7b49\u5f85\u4e0b\u8f7d', running: '\u4e0b\u8f7d\u4e2d', cancelling: '\u6b63\u5728\u505c\u6b62',
    cancelled: '\u5df2\u53d6\u6d88', completed: '\u5df2\u5b8c\u6210', failed: '\u4e0b\u8f7d\u5931\u8d25' };
  const text = (node, value) => { value = String(value ?? ''); if (node.textContent !== value) node.textContent = value; };
  function create(container) {
    const document = container.ownerDocument, cards = new Map();
    const empty = document.createElement('div'); empty.className = 'empty';
    text(empty, '\u4e0b\u8f7d\u961f\u5217\u8fd8\u662f\u7a7a\u7684');
    container.append(empty);
    return function render(tasks, options) {
      const ids = new Set(tasks.map(task => task.id));
      for (const [id, card] of cards) if (!ids.has(id)) { card.remove(); cards.delete(id); }
      empty.hidden = !!tasks.length;
      let previous = empty;
      for (const task of tasks) {
        let card = cards.get(task.id);
        if (!card) {
          card = document.createElement('article'); card.className = 'task-card'; card.dataset.taskId = task.id;
          card.innerHTML = '<div class="task-heading"><div class="task-title"></div><span class="task-speed"></span></div>' +
            '<div class="task-meta"><span data-status></span><span data-progress></span></div><progress max="100"></progress>' +
            '<div class="task-context"><span data-source></span><span data-directory></span><span data-wait></span></div>' +
            '<p class="task-error"><strong></strong><span></span></p><div class="task-actions"><span class="task-folder-error" role="status"></span>' +
            '<button data-cancel></button><button data-retry></button><button data-folder></button></div>' +
            '<details><summary></summary><pre></pre></details>';
          const find = selector => card.querySelector(selector);
          card.parts = { title: find('.task-title'), speed: find('.task-speed'), status: find('[data-status]'),
            progressText: find('[data-progress]'), progress: find('progress'), source: find('[data-source]'),
            directory: find('[data-directory]'), wait: find('[data-wait]'), error: find('.task-error'),
            errorLabel: find('.task-error strong'), errorText: find('.task-error span'), folderError: find('.task-folder-error'),
            cancel: find('[data-cancel]'), retry: find('[data-retry]'), folder: find('[data-folder]'), details: find('details'), log: find('pre') };
          const p = card.parts;
          p.speed.dataset.taskSpeed = task.id; p.cancel.dataset.cancel = task.id; p.retry.dataset.retry = task.id;
          p.folder.dataset.folder = task.id; p.details.dataset.log = task.id;
          text(p.cancel, '\u53d6\u6d88'); text(p.retry, '\u91cd\u8bd5'); text(p.folder, '\u6253\u5f00\u76ee\u5f55');
          text(find('summary'), '\u4efb\u52a1\u65e5\u5fd7');
          p.details.open = options.openedLogs.has(task.id);
          cards.set(task.id, card);
        }
        if (previous.nextSibling !== card) container.insertBefore(card, previous.nextSibling);
        previous = card;
        const p = card.parts, busy = ['running', 'cancelling'].includes(task.status);
        const progress = root.collectorTaskProgress(task);
        text(p.title, task.title); p.title.title = task.title || '';
        text(p.speed, root.collectorTaskSpeed(task)); p.speed.hidden = !busy;
        text(p.status, task.recovering ? '\u6b63\u5728\u91cd\u65b0\u89e3\u6790\u539f\u7d20\u6750' :
          `${labels[task.status] || task.status}${progress.stage ? ' \u00b7 ' + progress.stage : ''}`);
        p.status.classList.toggle('is-loading', busy || !!task.recovering);
        p.status.setAttribute('aria-busy', String(busy || !!task.recovering));
        text(p.progressText, progress.text);
        const value = progress.attribute.match(/value="([^"]+)"/)?.[1];
        if (value === undefined) p.progress.removeAttribute('value');
        else if (p.progress.getAttribute('value') !== value) p.progress.setAttribute('value', value);
        text(p.source, task.source ? '\u6765\u6e90\uff1a' + task.source : ''); p.source.hidden = !task.source;
        text(p.directory, task.targetDir ? '\u76ee\u5f55\uff1a' + task.targetDir : ''); p.directory.hidden = !task.targetDir;
        text(p.wait, task.waitReason); p.wait.hidden = !task.waitReason;
        p.error.hidden = !task.error;
        text(p.errorLabel, task.status === 'failed' ? '\u5931\u8d25\u539f\u56e0\uff1a' + (task.failure?.label || '\u539f\u56e0\u672a\u786e\u5b9a') : '');
        text(p.errorText, task.error);
        text(p.folderError, options.folderErrors.get(task.id)); p.folderError.hidden = !options.folderErrors.has(task.id);
        p.cancel.hidden = !task.recovering && !['queued', 'running'].includes(task.status);
        p.retry.hidden = !['failed', 'cancelled'].includes(task.status) || !(task.canRetry || task.recovering);
        p.retry.disabled = !!task.recovering;
        p.folder.hidden = task.status !== 'completed'; p.folder.disabled = options.openingFolders.has(task.id);
        const log = (task.logs || []).join('\n'), old = p.log.textContent;
        const selection = document.getSelection?.();
        const selected = selection && !selection.isCollapsed &&
          (p.log.contains(selection.anchorNode) || p.log.contains(selection.focusNode));
        if (log !== old) {
          if (log.startsWith(old) && p.log.lastChild?.nodeType === 3) p.log.lastChild.appendData(log.slice(old.length));
          else if (!selected) text(p.log, log);
        }
      }
    };
  }
  root.collectorRenderTasks = (container, tasks, options) => {
    if (!renderers.has(container)) renderers.set(container, create(container));
    renderers.get(container)(tasks, options);
  };
})(globalThis);
