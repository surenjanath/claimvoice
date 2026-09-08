/* The policy directory: who is on the books, and what to say to call in as
 * them. Demo data, so the last four digits are on show. */
(() => {
  const CFG = window.CLAIMVOICE;
  const holders = document.getElementById('holders');
  const search = document.getElementById('dir-search');
  const empty = document.getElementById('dir-empty');
  const countEl = document.getElementById('dir-count');
  let all = [];
  let filter = 'all';

  const escape = (value) =>
    String(value == null ? '' : value).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  const spoken = (value) => String(value || '').split('').join(' ');

  const initials = (name) =>
    String(name || '')
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase() || '?';

  const city = (address) => {
    const parts = String(address || '').split(',').map((p) => p.trim()).filter(Boolean);
    return parts[parts.length - 1] || '';
  };

  const haystack = (holder) => {
    const vehicles = (holder.vehicles || [])
      .map((v) => [v.year, v.make, v.model, v.colour, v.plate].join(' '))
      .join(' ');
    return [
      holder.full_name, holder.policy_number, holder.email, holder.address,
      holder.notes, holder.phone, holder.phone_last4, holder.coverage_label,
      holder.status_label, vehicles,
    ].join(' ').toLowerCase();
  };

  const matchesFilter = (holder) => {
    if (filter === 'active') return holder.status === 'active';
    if (filter === 'watch') return holder.status !== 'active';
    if (filter === 'liability') return holder.coverage === 'liability';
    if (filter === 'fleet') return (holder.vehicles || []).length > 1;
    if (filter === 'claims') return Number(holder.claims) > 0;
    return true;
  };

  function vehicleLine(v) {
    const name = [v.year, v.colour, v.make, v.model].filter(Boolean).join(' ');
    const plate = v.plate ? `<span class="plate mono">${escape(v.plate)}</span>` : '';
    return `<span class="vehicle">${escape(name)}${plate}</span>`;
  }

  function card(holder) {
    const vehicles = holder.vehicles || [];
    const featured = holder.policy_number === 'PV482193';
    const watch = holder.status !== 'active';
    const extras = [
      holder.roadside_assistance ? 'Roadside' : 'No roadside',
      holder.rental_cover ? 'Rental' : null,
    ].filter(Boolean);
    const renewal = holder.renewal_date
      ? new Date(holder.renewal_date + 'T00:00:00').toLocaleDateString(undefined, {
          month: 'short', day: 'numeric', year: 'numeric',
        })
      : '—';
    const el = document.createElement('article');
    el.className = 'card holder' + (featured ? ' featured' : '') + (watch ? ' watch' : '');
    el.id = holder.policy_number;
    el.innerHTML = `
      <div class="holder-head">
        <span class="avatar" aria-hidden="true">${escape(initials(holder.full_name))}</span>
        <div class="holder-who">
          <span class="holder-name">${escape(holder.full_name)}</span>
          <span class="holder-place">${escape(city(holder.address) || holder.address || '—')}</span>
        </div>
        <span class="pill ${holder.status === 'active' ? 'ok' : 'warn'}">${escape(holder.status_label)}</span>
      </div>
      ${featured ? '<p class="featured-tag mono">Recommended demo</p>' : ''}
      <div class="holder-creds">
        <button type="button" class="cred" data-copy="${escape(holder.policy_number)}">
          <span class="k mono">Policy</span>
          <b>${escape(holder.policy_number)}</b>
          <span class="copy-hint">Copy</span>
        </button>
        <button type="button" class="cred" data-copy="${escape(holder.phone_last4)}">
          <span class="k mono">Last four</span>
          <b>${escape(holder.phone_last4)}</b>
          <span class="copy-hint">Copy</span>
        </button>
      </div>
      <div class="holder-vehicles">
        ${vehicles.length ? vehicles.map(vehicleLine).join('') : '<span class="vehicle muted">No vehicle on file</span>'}
      </div>
      <div class="holder-rows">
        <div><span class="k mono">Cover</span><span>${escape(holder.coverage_label)} · $${escape(holder.deductible)} deductible</span></div>
        <div><span class="k mono">Extras</span><span>${extras.map(escape).join(' · ')}</span></div>
        <div><span class="k mono">Renews</span><span>${escape(renewal)}</span></div>
        <div><span class="k mono">History</span><span>${escape(holder.claims)} prior claim${Number(holder.claims) === 1 ? '' : 's'}${holder.calls ? ` · ${escape(holder.calls)} call${Number(holder.calls) === 1 ? '' : 's'}` : ''}</span></div>
        ${holder.notes ? `<div><span class="k mono">Note</span><span class="note">${escape(holder.notes)}</span></div>` : ''}
      </div>
      <p class="say">Say: “My policy number is ${escape(spoken(holder.policy_number))}”
      then “${escape(spoken(holder.phone_last4))}”.</p>
      <div class="holder-foot">
        <a class="ghost" href="${CFG.voiceUrl}#${escape(holder.policy_number)}">Talk as ${escape(holder.first_name || holder.full_name)}</a>
        <a class="ghost" href="/dashboard/#policy-${escape(holder.policy_number)}">Claims on the board</a>
        ${Number(holder.claims) > 0
          ? `<button type="button" class="ghost" data-history="${escape(holder.policy_number)}">Show ${escape(holder.claims)} claim${Number(holder.claims) === 1 ? '' : 's'}</button>`
          : ''}
      </div>
      <div class="holder-history" hidden></div>
    `;
    return el;
  }

  function paintKpis() {
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('kpi-book', all.length);
    set('kpi-active', all.filter((h) => h.status === 'active').length);
    set('kpi-watch', all.filter((h) => h.status !== 'active').length);
    set('kpi-vehicles', all.reduce((n, h) => n + (h.vehicles || []).length, 0));
  }

  function shown() {
    const term = (search.value || '').trim().toLowerCase();
    return all.filter((h) => matchesFilter(h) && (!term || haystack(h).includes(term)));
  }

  function render() {
    const rows = shown();
    holders.replaceChildren(...rows.map(card));
    if (empty) empty.hidden = rows.length > 0 || !all.length;
    if (countEl) {
      countEl.textContent = all.length
        ? `${rows.length} of ${all.length}`
        : '';
    }
    if (!all.length) {
      holders.innerHTML = '<p class="empty">The book is empty. Run <code>python manage.py seed_policies</code>.</p>';
    }
    bindCopy();
    bindHistory();
    highlightHash();
  }

  function bindHistory() {
    holders.querySelectorAll('[data-history]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const box = btn.closest('.holder').querySelector('.holder-history');
        if (!box || !CFG.claimsUrl) return;
        if (!box.hidden && box.dataset.loaded) {
          box.hidden = true;
          return;
        }
        box.hidden = false;
        box.textContent = 'Loading…';
        try {
          const res = await fetch(`${CFG.claimsUrl}?policy=${encodeURIComponent(btn.dataset.history)}`);
          const data = await res.json();
          const rows = data.claims || [];
          box.dataset.loaded = '1';
          box.innerHTML = rows.length
            ? rows.map((claim) =>
                `<a href="/dashboard/#claim-${claim.id}">CV-${String(claim.id).padStart(5, '0')}</a>
                 <span class="muted">${escape(claim.incident_label)} · ${escape(claim.location)}</span>`
              ).join('')
            : '<span class="muted">No claims on file.</span>';
        } catch (error) {
          box.textContent = 'Could not load claims.';
        }
      });
    });
  }

  function bindCopy() {
    holders.querySelectorAll('[data-copy]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { /* ignore */ }
        const hint = btn.querySelector('.copy-hint') || btn;
        const prior = hint.textContent;
        hint.textContent = 'Copied';
        setTimeout(() => { hint.textContent = prior === 'Copied' ? 'Copy' : prior; }, 1400);
      });
    });
  }

  function highlightHash() {
    const id = (location.hash || '').replace(/^#/, '');
    if (!id) return;
    const card = document.getElementById(id);
    if (!card) return;
    card.classList.add('flash');
    card.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  search.addEventListener('input', render);
  document.getElementById('dir-filters').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-filter]');
    if (!chip) return;
    filter = chip.dataset.filter;
    document.querySelectorAll('#dir-filters .chip').forEach((el) => {
      el.classList.toggle('on', el === chip);
    });
    render();
  });
  window.addEventListener('hashchange', highlightHash);

  fetch(CFG.policyholdersUrl)
    .then((res) => res.json())
    .then((data) => {
      all = data.policyholders || [];
      paintKpis();
      render();
    })
    .catch(() => {
      holders.innerHTML = '<p class="empty">Could not load the directory.</p>';
    });
})();
