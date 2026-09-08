/* Agent settings: pane switcher, dirty tracking, presets, range labels. */
(() => {
  const form = document.getElementById('settings-form');
  if (!form) return;

  const panes = [...form.querySelectorAll('.pane')];
  const nav = [...form.querySelectorAll('.nav-item')];
  const unsavedPill = document.getElementById('pill-unsaved');
  const draftPill = document.getElementById('pill-draft');
  const snapshot = new FormData(form);
  let dirty = false;

  function snapshotOf(data) {
    return [...data.entries()]
      .filter(([key]) => key !== 'csrfmiddlewaretoken' && key !== 'action')
      .map(([key, value]) => `${key}=${value}`)
      .sort()
      .join('\n');
  }

  const baseline = snapshotOf(snapshot);

  function isDirty() {
    return snapshotOf(new FormData(form)) !== baseline;
  }

  function paintDirty() {
    dirty = isDirty();
    if (unsavedPill) unsavedPill.hidden = !dirty;
    if (draftPill && dirty) draftPill.hidden = true;
    else if (draftPill) draftPill.hidden = false;
  }

  function showPane(id) {
    const pane = id || 'identity';
    panes.forEach((el) => {
      el.hidden = el.dataset.pane !== pane;
    });
    nav.forEach((btn) => {
      btn.classList.toggle('on', btn.dataset.pane === pane);
    });
    if (location.hash !== `#${pane}`) {
      history.replaceState(null, '', `#${pane}`);
    }
  }

  function paneFromHash() {
    const raw = (location.hash || '').replace('#', '');
    return panes.some((el) => el.dataset.pane === raw) ? raw : 'identity';
  }

  nav.forEach((btn) => {
    btn.addEventListener('click', () => showPane(btn.dataset.pane));
  });
  window.addEventListener('hashchange', () => showPane(paneFromHash()));

  const start = form.dataset.errorPane || paneFromHash();
  showPane(start);

  form.addEventListener('input', paintDirty);
  form.addEventListener('change', paintDirty);

  window.addEventListener('beforeunload', (event) => {
    if (dirty) event.preventDefault();
  });
  form.querySelectorAll('[data-skip-dirty]').forEach((btn) => {
    btn.addEventListener('click', () => { dirty = false; });
  });
  form.addEventListener('submit', () => { dirty = false; });

  const copy = document.getElementById('copy-link');
  const share = document.getElementById('share-link');
  if (copy && share) {
    copy.addEventListener('click', async () => {
      share.select();
      try { await navigator.clipboard.writeText(share.value); } catch { document.execCommand('copy'); }
      copy.textContent = 'Copied';
      setTimeout(() => { copy.textContent = 'Copy'; }, 1600);
    });
  }

  form.querySelectorAll('input[type=range]').forEach((range) => {
    const field = range.closest('.field');
    const label = field && field.querySelector('label');
    const out = document.createElement('span');
    out.className = 'range-value mono';
    const unit = range.name.includes('silence') ? 'ms' : '';
    const paint = () => {
      out.textContent = unit ? `${range.value}${unit}` : range.value;
    };
    if (label) label.appendChild(out);
    else range.after(out);
    range.addEventListener('input', paint);
    paint();
  });

  form.querySelectorAll('.voice-card input').forEach((radio) => {
    radio.addEventListener('change', () => {
      form.querySelectorAll('.voice-card').forEach((card) => {
        card.classList.toggle('on', card.querySelector('input').checked);
      });
    });
  });

  const presetNode = document.getElementById('prompt-presets');
  const prompt = form.querySelector('[name=system_prompt]');
  const presets = presetNode ? JSON.parse(presetNode.textContent) : [];
  form.querySelectorAll('[data-preset]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const preset = presets.find((item) => item.id === btn.dataset.preset);
      if (!preset || !prompt) return;
      const current = prompt.value.trim();
      if (current && current !== preset.prompt.trim()) {
        const ok = window.confirm(
          `Replace the current prompt with “${preset.label}”? This cannot be undone except by Reset.`
        );
        if (!ok) return;
      }
      prompt.value = preset.prompt;
      form.querySelectorAll('[data-preset]').forEach((chip) => {
        chip.classList.toggle('on', chip === btn);
      });
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });

  if (prompt && presets.length) {
    const match = presets.find((item) => item.prompt.trim() === prompt.value.trim());
    if (match) {
      const chip = form.querySelector(`[data-preset="${match.id}"]`);
      if (chip) chip.classList.add('on');
    }
  }

  form.querySelectorAll('[data-restore]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const ok = window.confirm('Restore this published prompt into the draft? Publish again to push it live.');
      if (!ok) return;
      const token = form.querySelector('[name=csrfmiddlewaretoken]');
      const res = await fetch(`/api/revisions/${btn.dataset.restore}/restore/`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': token ? token.value : '',
          Accept: 'application/json',
        },
      });
      if (res.ok) location.reload();
    });
  });

  const syncBtn = document.getElementById('sync-calls');
  const syncOut = document.getElementById('sync-result');
  if (syncBtn) {
    syncBtn.addEventListener('click', async () => {
      const token = form.querySelector('[name=csrfmiddlewaretoken]');
      syncBtn.disabled = true;
      syncBtn.textContent = 'Syncing…';
      try {
        const res = await fetch('/api/sync-calls/', {
          method: 'POST',
          headers: {
            'X-CSRFToken': token ? token.value : '',
            Accept: 'application/json',
          },
        });
        const data = await res.json();
        if (syncOut) {
          syncOut.hidden = false;
          syncOut.textContent = res.ok
            ? `${data.sessions || 0} sessions · ${data.created || 0} new · ${data.updated || 0} updated`
            : (data.error || 'Sync failed');
        }
      } catch (error) {
        if (syncOut) {
          syncOut.hidden = false;
          syncOut.textContent = 'Could not reach the server.';
        }
      }
      syncBtn.disabled = false;
      syncBtn.textContent = 'Sync phone calls';
    });
  }

  paintDirty();
})();
