/* The policy directory: who is on the books, and what to say to call in as
 * them. Demo data, so the last four digits are on show. */
(() => {
  const CFG = window.CLAIMVOICE;
  const holders = document.getElementById('holders');
  const search = document.getElementById('dir-search');
  let all = [];

  const escape = (value) =>
    String(value == null ? '' : value).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  function card(holder) {
    const vehicles = (holder.vehicles || [])
      .map((v) => `${v.year || ''} ${v.colour || ''} ${v.make || ''} ${v.model || ''}`.trim())
      .filter(Boolean);
    const el = document.createElement('div');
    el.className = 'card holder';
    el.innerHTML = `
      <div class="holder-head">
        <span class="holder-name">${escape(holder.full_name)}</span>
        <span class="pill ${holder.status === 'active' ? 'ok' : 'warn'}">${escape(holder.status_label)}</span>
      </div>
      <div class="holder-creds">
        <span class="cred"><span class="k mono">Policy</span><b>${escape(holder.policy_number)}</b></span>
        <span class="cred"><span class="k mono">Last four</span><b>${escape(holder.phone_last4)}</b></span>
      </div>
      <div class="holder-rows">
        <div><span class="k mono">Vehicles</span><span>${vehicles.map(escape).join('<br>') || '—'}</span></div>
        <div><span class="k mono">Cover</span><span>${escape(holder.coverage_label)} · $${escape(holder.deductible)} deductible</span></div>
        <div><span class="k mono">Extras</span><span>${[
          holder.roadside_assistance ? 'Roadside' : 'No roadside',
          holder.rental_cover ? 'Rental cover' : null,
        ].filter(Boolean).map(escape).join(' · ')}</span></div>
        <div><span class="k mono">Claims</span><span>${escape(holder.claims)}</span></div>
        ${holder.notes ? `<div><span class="k mono">Note</span><span class="note">${escape(holder.notes)}</span></div>` : ''}
      </div>
      <p class="say">Say: “My policy number is ${escape(holder.policy_number.split('').join(' '))}”
      then “${escape(holder.phone_last4.split('').join(' '))}”.</p>
    `;
    return el;
  }

  function render() {
    const term = (search.value || '').trim().toLowerCase();
    const shown = term
      ? all.filter((h) =>
          `${h.full_name} ${h.policy_number} ${h.email}`.toLowerCase().includes(term)
        )
      : all;
    holders.replaceChildren(...shown.map(card));
  }

  search.addEventListener('input', render);

  fetch(CFG.policyholdersUrl)
    .then((res) => res.json())
    .then((data) => {
      all = data.policyholders;
      render();
    })
    .catch(() => {
      holders.innerHTML = '<p class="empty">Could not load the directory.</p>';
    });
})();
