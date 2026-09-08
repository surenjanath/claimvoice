/* The dispatcher board.
 *
 * Two views over the same poll: the claims that came out of calls, and the
 * calls themselves. Claims poll incrementally — once the full list is in, it
 * only asks for rows newer than the highest id it has seen, so the steady
 * state is an empty response and the table only moves when something lands.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const CFG = window.CLAIMVOICE;
  const POLL_MS = 2000;
  let vendorBook = [];

  let claims = [];
  let conversations = [];
  let dispatches = [];
  let latestId = 0;
  let filter = 'all';
  let convFilter = 'all';
  let dispatchFilter = 'open';
  let view = 'claims';
  let showUnpinned = false;
  let term = '';
  let hashReady = false;
  const fresh = new Set();

  const escape = (value) =>
    String(value == null ? '' : value).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  function timeAgo(iso) {
    const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    return Math.floor(seconds / 86400) + 'd ago';
  }

  const reference = (id) => 'CV-' + String(id).padStart(5, '0');
  const clock = (seconds) => {
    const whole = Math.round(seconds || 0);
    return Math.floor(whole / 60) + ':' + String(whole % 60).padStart(2, '0');
  };

  // --- claims ---------------------------------------------------------------

  function matches(claim) {
    if (term && !`${claim.policy_number} ${claim.location} ${claim.caller_name} ${claim.vehicle} ${claim.assigned_to || ''}`
      .toLowerCase().includes(term)) return false;
    if (filter === 'all') return true;
    if (filter === 'critical') return claim.priority === 'critical';
    if (filter === 'handoff') return claim.needs_human;
    if (filter === 'lapsed') return (claim.flags || []).includes('lapsed');
    if (filter === 'fraud') return (claim.flags || []).includes('fraud watch');
    if (filter === 'unverified') return !claim.verified;
    if (filter === 'mine') return Boolean(claim.assigned_to);
    if (filter === 'open') return !claim.assigned_to;
    if (filter === 'waiting') {
      return claim.status === 'new' && (Date.now() - new Date(claim.created_at).getTime()) > 20 * 60 * 1000;
    }
    return claim.status === filter;
  }

  function claimRow(claim) {
    const tr = document.createElement('tr');
    tr.dataset.id = claim.id;
    tr.className = fresh.has(claim.id) ? 'fresh clickable' : 'clickable';
    const factors = (claim.risk_factors || []).slice(0, 3).join(' · ');

    tr.innerHTML = `
      <td><span class="ref">${escape(reference(claim.id))}</span></td>
      <td>
        <div class="policy">${escape(claim.policy_number)}</div>
        ${claim.verified
          ? '<span class="tag verified">Verified</span>'
          : '<span class="tag unverified">Unverified</span>'}
        ${claim.assigned_to ? `<span class="tag">${escape(claim.assigned_to)}</span>` : ''}
        ${(claim.flags || []).filter((flag) => flag !== 'unverified' && flag !== 'owned').map((flag) =>
          `<span class="tag flag">${escape(flag)}</span>`).join('')}
      </td>
      <td><span class="tag">${escape(claim.incident_label)}</span></td>
      <td>
        <div class="where">${escape(claim.location)}</div>
        <div class="who-line">${[claim.policyholder_name || claim.caller_name, claim.vehicle].filter(Boolean).map(escape).join(' · ')}</div>
        ${(claim.dispatches || []).length
          ? `<div class="who-line">${escape(claim.dispatches.map((row) => row.kind_label).join(' · '))}</div>`
          : ''}
        ${claim.description ? `<div class="desc">${escape(claim.description)}</div>` : ''}
      </td>
      <td>
        <div class="risk p-${escape(claim.priority)}">
          <span class="num">${escape(claim.risk_score)}</span>
          <span class="bar"><span style="width:${Number(claim.risk_score)}%"></span></span>
        </div>
        ${factors ? `<div class="factors">${escape(factors)}</div>` : ''}
      </td>
      <td>
        ${claim.is_drivable
          ? '<span class="tag drivable">Drivable</span>'
          : '<span class="tag tow">Tow needed</span>'}
        ${claim.injuries_reported ? '<span class="tag injury">Injuries</span>' : ''}
        ${claim.severity && claim.severity !== 'none' ? `<span class="tag">${escape(claim.severity_label)}</span>` : ''}
        ${claim.vehicle_mismatch ? '<span class="tag flag">Mismatch</span>' : ''}
        ${claim.roadside_assistance === false && claim.tow_required ? '<span class="tag flag">No roadside</span>' : ''}
      </td>
      <td>
        <div class="state">
          ${['new', 'dispatched', 'closed']
            .map((state) =>
              `<button data-status="${state}" class="${claim.status === state ? 'on' : ''}">${state}</button>`)
            .join('')}
        </div>
      </td>
      <td><span class="when">${escape(timeAgo(claim.created_at))}</span></td>
    `;

    tr.querySelectorAll('.state button').forEach((button) => {
      button.onclick = (event) => {
        event.stopPropagation();
        setStatus(claim.id, button.dataset.status);
      };
    });
    tr.onclick = () => openClaim(claim);
    return tr;
  }

  // --- conversations --------------------------------------------------------

  function convMatches(conversation) {
    const who = conversation.policyholder ? conversation.policyholder.full_name : '';
    if (term && !`${who} ${conversation.summary} ${conversation.caller_number}`
      .toLowerCase().includes(term)) return false;
    if (convFilter === 'verified') return conversation.verified;
    if (convFilter === 'unverified') return !conversation.verified;
    if (convFilter === 'handoff') return conversation.needs_human;
    if (convFilter === 'live') return !conversation.ended_at;
    return true;
  }

  function convRow(conversation) {
    const tr = document.createElement('tr');
    tr.className = 'clickable';
    const who = conversation.policyholder
      ? conversation.policyholder.full_name
      : conversation.caller_number || 'Unidentified';
    tr.innerHTML = `
      <td><span class="ref">#${escape(conversation.id)}</span></td>
      <td>
        <div class="where">${escape(who)}</div>
        ${conversation.verified
          ? '<span class="tag verified">Verified</span>'
          : '<span class="tag unverified">Unverified</span>'}
        ${conversation.needs_human ? '<span class="tag flag">Handoff</span>' : ''}
        ${conversation.ended_at ? '' : '<span class="tag live">Live</span>'}
      </td>
      <td><span class="tag">${escape(conversation.channel_label)}</span></td>
      <td><div class="desc" style="max-width:52ch">${escape(conversation.summary) || '<span class="muted">no transcript</span>'}</div></td>
      <td>
        <span class="when">${escape(conversation.turn_count)} turns · ${escape(conversation.tool_count)} tools</span>
        ${conversation.recording_url ? '<span class="tag audio">Audio</span>' : ''}
      </td>
      <td>${conversation.claim_reference
        ? `<span class="ref">${escape(conversation.claim_reference)}</span>`
        : '<span class="muted">—</span>'}</td>
      <td><span class="when">${escape(clock(conversation.duration_seconds))}</span></td>
      <td><span class="when">${escape(timeAgo(conversation.started_at))}</span></td>
    `;
    tr.onclick = () => openConversation(conversation.id);
    return tr;
  }

  // --- dispatches -----------------------------------------------------------

  function dispatchMatches(row) {
    const blob = `${row.policyholder} ${row.location} ${row.claim_reference} ${row.vendor} ${row.policy_number}`.toLowerCase();
    if (term && !blob.includes(term)) return false;
    if (dispatchFilter === 'open') {
      return row.status !== 'arrived' && row.status !== 'cancelled';
    }
    if (dispatchFilter === 'all') return true;
    return row.kind === dispatchFilter;
  }

  function etaLabel(row) {
    if (row.status === 'arrived') return 'arrived';
    if (row.status === 'cancelled') return '—';
    const left = remainingMinutes(row);
    if (left == null) return '—';
    if (left === 0) return 'due now';
    return left + 'm left';
  }

  function remainingMinutes(row) {
    if (row.eta_minutes == null) return null;
    if (row.status === 'arrived') return 0;
    if (row.status === 'cancelled') return null;
    const elapsed = (Date.now() - new Date(row.created_at).getTime()) / 60000;
    return Math.max(0, Math.round(row.eta_minutes - elapsed));
  }

  function dispatchRow(row) {
    const tr = document.createElement('tr');
    tr.className = 'clickable';
    const phone = row.vendor_phone
      ? `<a class="who-line tel" href="tel:${escape(row.vendor_phone)}">${escape(row.vendor_phone)}</a>`
      : '';
    const left = remainingMinutes(row);
    tr.innerHTML = `
      <td><span class="tag">${escape(row.kind_label)}</span></td>
      <td><span class="ref">${escape(row.claim_reference)}</span></td>
      <td>
        <div class="where">${escape(row.policyholder || '—')}</div>
        <div class="who-line">${escape(row.policy_number || '')}</div>
      </td>
      <td><div class="where">${escape(row.location || '—')}</div></td>
      <td>
        <div class="where">${escape(row.vendor || '—')}</div>
        ${phone}
      </td>
      <td><span class="when ${left === 0 && row.status !== 'arrived' ? 'due' : ''}">${escape(etaLabel(row))}</span></td>
      <td>
        <div class="state">
          ${['requested', 'en_route', 'arrived', 'cancelled']
            .map((state) =>
              `<button data-dstatus="${state}" data-did="${row.id}" class="${row.status === state ? 'on' : ''}">${state.replace('_', ' ')}</button>`)
            .join('')}
        </div>
      </td>
      <td><span class="when">${escape(timeAgo(row.created_at))}</span></td>
    `;
    tr.querySelectorAll('[data-dstatus]').forEach((button) => {
      button.onclick = (event) => {
        event.stopPropagation();
        setDispatchStatus(row.id, button.dataset.dstatus);
      };
    });
    tr.querySelectorAll('a.tel').forEach((link) => {
      link.onclick = (event) => event.stopPropagation();
    });
    tr.onclick = () => {
      const claim = claims.find((c) => c.id === row.claim_id);
      if (claim) {
        showView('claims');
        openClaim(claim);
      }
    };
    return tr;
  }

  // --- drawer ---------------------------------------------------------------

  function openDrawer(title) {
    if (drawerAudio) {
      drawerAudio.pause();
      drawerAudio = null;
    }
    $('drawer-title').textContent = title;
    $('drawer-body').replaceChildren();
    $('drawer').hidden = false;
    $('scrim').hidden = false;
  }

  let drawerAudio = null;

  function closeDrawer() {
    if (drawerAudio) {
      drawerAudio.pause();
      drawerAudio = null;
    }
    $('drawer').hidden = true;
    $('scrim').hidden = true;
  }
  $('drawer-close').onclick = closeDrawer;
  $('scrim').onclick = closeDrawer;
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeDrawer();
  });

  function detailRows(pairs) {
    const wrap = document.createElement('div');
    wrap.className = 'detail';
    wrap.innerHTML = pairs
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `<div><span class="k mono">${escape(key)}</span><span>${value}</span></div>`)
      .join('');
    return wrap;
  }

  function openClaim(claim) {
    openDrawer(reference(claim.id));
    const body = $('drawer-body');
    body.append(
      detailRows([
        ['Policy', escape(claim.policy_number)],
        ['Policyholder', escape(claim.policyholder_name || claim.caller_name || '—')],
        ['Verified', claim.verified ? 'Yes, on the call' : 'No — caller not verified'],
        ['Incident', escape(claim.incident_label)],
        ['Location', escape(claim.location)],
        ['Vehicle', escape(claim.vehicle || '—')],
        ['Severity', escape(claim.severity_label || '—')],
        ['Drivable', claim.is_drivable ? 'Yes' : 'No — tow required'],
        ['Injuries', claim.injuries_reported === null ? 'Not stated' : claim.injuries_reported ? 'Yes' : 'None'],
        ['Risk', `${escape(claim.risk_score)} (${escape(claim.priority)})`],
        ['Why', escape((claim.risk_factors || []).join(' · '))],
        ['Status', escape(claim.status_label)],
        ['Filed', new Date(claim.created_at).toLocaleString()],
        ['Source', escape(claim.source)],
        ['Share', claim.share_path
          ? `<a href="${escape(claim.share_path)}">${escape(reference(claim.id))}</a>`
          : '—'],
        ['Handoff', claim.needs_human ? escape(claim.handoff_reason || 'requested') : ''],
        ['Cover', escape(claim.coverage_label || '')],
        ['Deductible', claim.deductible != null ? '$' + escape(claim.deductible) : ''],
        ['Roadside', claim.roadside_assistance == null ? '' : claim.roadside_assistance ? 'Included' : 'Not on this policy'],
        ['Rental', claim.rental_cover ? 'Included' : ''],
        ['On file', escape(claim.vehicle_on_file || '')],
        ['Said', claim.vehicle_mismatch ? `<span class="flag">${escape(claim.vehicle || '')}</span>` : escape(claim.vehicle || '')],
      ])
    );
    if ((claim.flags || []).length) {
      const flags = document.createElement('div');
      flags.className = 'checks';
      flags.innerHTML = claim.flags.map((flag) => `<span class="tag flag">${escape(flag)}</span>`).join('');
      body.append(flags);
    }
    if (claim.description) {
      const note = document.createElement('p');
      note.className = 'drawer-note';
      note.textContent = claim.description;
      body.append(note);
    }
    const siblings = claims.filter((row) =>
      row.id !== claim.id && claim.policy_number && row.policy_number === claim.policy_number
    );
    if (siblings.length) {
      const related = document.createElement('div');
      related.className = 'siblings';
      related.innerHTML = siblings.map((row) =>
        `<a href="#claim-${row.id}">${escape(reference(row.id))} · ${escape(row.status_label)}</a>`
      ).join('');
      body.append(related);
    }

    body.append(sharePanel(claim));
    body.append(notePanel(claim));
    body.append(photoPanel(claim));
    body.append(dispatchPanel(claim));
    const footer = document.createElement('div');
    footer.className = 'actions';
    if (claim.conversation_id) {
      const link = document.createElement('button');
      link.className = 'tool-btn';
      link.textContent = 'Open the call';
      link.onclick = () => openConversation(claim.conversation_id);
      footer.append(link);
    }
    if (footer.childElementCount) body.append(footer);
  }

  function sharePanel(claim) {
    const wrap = document.createElement('div');
    wrap.className = 'dispatch-panel';
    const heading = document.createElement('div');
    heading.className = 'k mono';
    heading.style.margin = '24px 0 10px';
    heading.textContent = 'Caller share link';
    wrap.append(heading);

    const row = document.createElement('div');
    row.className = 'link-row';
    const path = claim.share_path;
    if (!path) return;
    row.innerHTML = `
      <input type="text" readonly value="${escape(location.origin + path)}">
      <a class="tool-btn" href="${escape(path)}" target="_blank" rel="noopener">Open</a>
      <button class="tool-btn" type="button">Copy</button>`;
    row.querySelector('button').onclick = async () => {
      const input = row.querySelector('input');
      try { await navigator.clipboard.writeText(input.value); } catch { input.select(); document.execCommand('copy'); }
      row.querySelector('button').textContent = 'Copied';
      setTimeout(() => { row.querySelector('button').textContent = 'Copy'; }, 1400);
    };
    wrap.append(row);

    const sms = document.createElement('div');
    sms.className = 'sms-line';
    const smsLabel = {
      sent: 'Text sent to the number on the policy',
      failed: 'Text failed — try again',
      unset: 'SMS is not configured',
      no_number: 'No number on the policy',
      skipped: 'Text was skipped',
    }[claim.sms_status] || 'No text sent yet';
    const emailLabel = {
      sent: 'Email sent to the address on the policy',
      failed: 'Email failed',
      unset: 'Email is not configured',
      no_email: 'No email on the policy',
      skipped: 'Email was skipped',
    }[claim.email_status] || 'No email sent yet';
    sms.innerHTML = `
      <span class="tag ${claim.sms_status === 'sent' ? 'verified' : ''}">${escape(smsLabel)}</span>
      <span class="tag ${claim.email_status === 'sent' ? 'verified' : ''}">${escape(emailLabel)}</span>`;
    const tools = document.createElement('div');
    tools.className = 'actions';
    if (CFG.notifyUrl) {
      const resend = document.createElement('button');
      resend.className = 'tool-btn';
      resend.textContent = (claim.sms_status === 'sent' || claim.email_status === 'sent')
        ? 'Resend notice'
        : 'Send text / email';
      resend.onclick = async () => {
        resend.disabled = true;
        try {
          const res = await fetch(CFG.notifyUrl.replace('__ID__', claim.id), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
          });
          const body = await res.json();
          if (body.claim) {
            const index = claims.findIndex((c) => c.id === body.claim.id);
            if (index > -1) claims[index] = body.claim;
            openClaim(body.claim);
          }
        } finally {
          resend.disabled = false;
        }
      };
      tools.append(resend);
    }
    wrap.append(sms);
    const copyRef = document.createElement('button');
    copyRef.className = 'tool-btn';
    copyRef.textContent = 'Copy ' + reference(claim.id);
    copyRef.onclick = async () => {
      try { await navigator.clipboard.writeText(reference(claim.id)); } catch { /* ignore */ }
      copyRef.textContent = 'Copied';
      setTimeout(() => { copyRef.textContent = 'Copy ' + reference(claim.id); }, 1400);
    };
    tools.append(copyRef);
    const printBtn = document.createElement('button');
    printBtn.className = 'tool-btn';
    printBtn.textContent = 'Print packet';
    printBtn.onclick = () => window.print();
    tools.append(printBtn);

    if (CFG.assignUrl) {
      const assign = document.createElement('button');
      assign.className = 'tool-btn';
      assign.textContent = claim.assigned_to ? 'Release' : 'Take this claim';
      assign.onclick = async () => {
        assign.disabled = true;
        try {
          const res = await fetch(CFG.assignUrl.replace('__ID__', claim.id), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
            body: JSON.stringify(claim.assigned_to ? { release: true } : { assigned_to: CFG.deskName || 'Dispatcher' }),
          });
          const body = await res.json();
          if (body.claim) {
            const index = claims.findIndex((c) => c.id === body.claim.id);
            if (index > -1) claims[index] = body.claim;
            openClaim(body.claim);
            render();
          }
        } finally {
          assign.disabled = false;
        }
      };
      tools.append(assign);
    }

    if (!claim.needs_human) {
      const handoff = document.createElement('button');
      handoff.className = 'tool-btn';
      handoff.textContent = 'Request a human';
      handoff.onclick = async () => {
        handoff.disabled = true;
        try {
          const res = await fetch(CFG.handoffUrl.replace('__ID__', claim.id), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
            body: JSON.stringify({ reason: 'dispatcher' }),
          });
          if (!res.ok) return;
          const { claim: next } = await res.json();
          const index = claims.findIndex((c) => c.id === next.id);
          if (index > -1) claims[index] = next;
          openClaim(next);
          render();
        } finally {
          handoff.disabled = false;
        }
      };
      tools.append(handoff);
    }
    wrap.append(tools);
    return wrap;
  }

  function notePanel(claim) {
    const wrap = document.createElement('div');
    wrap.className = 'dispatch-panel';
    const heading = document.createElement('div');
    heading.className = 'k mono';
    heading.style.margin = '24px 0 10px';
    heading.textContent = 'Notes';
    wrap.append(heading);
    const list = document.createElement('div');
    list.className = 'note-list';
    const rows = claim.notes || [];
    list.innerHTML = rows.length
      ? rows.map((note) => `<div class="note-item"><b>${escape(note.author)}</b>${escape(note.body)}</div>`).join('')
      : '<p class="empty">No notes yet.</p>';
    wrap.append(list);
    if (!CFG.noteUrl) return wrap;
    const form = document.createElement('form');
    form.className = 'note-form';
    form.innerHTML = `
      <input name="body" placeholder="Add a dispatcher note…" maxlength="400" required>
      <button class="btn small quiet" type="submit">Add</button>`;
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = form.querySelector('button');
      button.disabled = true;
      try {
        const res = await fetch(CFG.noteUrl.replace('__ID__', claim.id), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
            'X-CSRFToken': CFG.csrfToken,
          },
          body: JSON.stringify({ body: form.body.value, author: 'dispatcher' }),
        });
        const data = await res.json();
        if (data.claim) {
          const index = claims.findIndex((c) => c.id === data.claim.id);
          if (index > -1) claims[index] = data.claim;
          openClaim(data.claim);
        }
      } finally {
        button.disabled = false;
      }
    };
    wrap.append(form);
    return wrap;
  }

  function photoPanel(claim) {
    const wrap = document.createElement('div');
    wrap.className = 'dispatch-panel';
    const heading = document.createElement('div');
    heading.className = 'k mono';
    heading.style.margin = '24px 0 10px';
    heading.textContent = 'Photos';
    wrap.append(heading);

    const grid = document.createElement('div');
    grid.className = 'photo-grid compact';
    wrap.append(grid);

    const paint = (rows) => {
      grid.replaceChildren();
      if (!rows.length) {
        const none = document.createElement('p');
        none.className = 'empty';
        none.textContent = 'No photos yet.';
        grid.append(none);
        return;
      }
      for (const photo of rows) {
        const fig = document.createElement('figure');
        fig.innerHTML = `<img src="${escape(photo.url)}" alt="${escape(photo.caption || 'Claim photo')}">
          ${photo.caption ? `<figcaption>${escape(photo.caption)}</figcaption>` : ''}`;
        grid.append(fig);
      }
    };

    fetch(CFG.photosUrl.replace('__ID__', claim.id))
      .then((res) => res.json())
      .then((body) => paint(body.photos || []))
      .catch(() => paint([]));

    const form = document.createElement('form');
    form.className = 'photo-form';
    form.innerHTML = `
      <label class="file-pick">
        <input type="file" name="photo" accept="image/*" required>
        <span class="file-name">Choose a photo</span>
      </label>
      <input type="text" name="caption" placeholder="Optional caption" maxlength="120">
      <button class="btn small quiet" type="submit">Upload</button>
      <p class="form-error" hidden></p>`;
    const pick = form.querySelector('.file-pick');
    const fileInput = form.querySelector('input[type=file]');
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      pick.querySelector('.file-name').textContent = file ? file.name : 'Choose a photo';
      pick.classList.toggle('has-file', Boolean(file));
    });
    const error = form.querySelector('.form-error');
    form.onsubmit = async (event) => {
      event.preventDefault();
      error.hidden = true;
      const data = new FormData(form);
      const button = form.querySelector('button');
      button.disabled = true;
      try {
        const res = await fetch(CFG.photoUploadUrl.replace('__ID__', claim.id), {
          method: 'POST',
          headers: { 'X-CSRFToken': CFG.csrfToken, Accept: 'application/json' },
          body: data,
        });
        const body = await res.json();
        if (!res.ok) {
          error.textContent = body.error || 'Could not upload that.';
          error.hidden = false;
          return;
        }
        form.reset();
        const feed = await fetch(CFG.photosUrl.replace('__ID__', claim.id)).then((r) => r.json());
        paint(feed.photos || []);
        const index = claims.findIndex((c) => c.id === claim.id);
        if (index > -1) claims[index].photo_count = (feed.photos || []).length;
      } catch (exception) {
        error.textContent = 'Could not reach the server.';
        error.hidden = false;
      } finally {
        button.disabled = false;
      }
    };
    wrap.append(form);
    return wrap;
  }

  // --- dispatch -------------------------------------------------------------
  // The webhook raises the obvious tow by itself. Everything else is a
  // judgement call — a second truck, an adjuster, a locksmith — so a dispatcher
  // raises it here and it lands on the same claim.
  const KINDS = [
    ['tow', 'Tow truck'],
    ['adjuster', 'Adjuster'],
    ['investigator', 'Theft investigator'],
    ['locksmith', 'Locksmith'],
    ['rental', 'Replacement vehicle'],
    ['ambulance', 'Medical'],
  ];

  function dispatchPanel(claim) {
    const wrap = document.createElement('div');
    wrap.className = 'dispatch-panel';

    const heading = document.createElement('div');
    heading.className = 'k mono';
    heading.style.margin = '24px 0 10px';
    heading.textContent = 'Dispatched to the scene';
    wrap.append(heading);

    const list = document.createElement('div');
    list.className = 'dispatch-list';
    wrap.append(list);

    const paint = (rows) => {
      list.replaceChildren();
      if (!rows.length) {
        const none = document.createElement('p');
        none.className = 'empty';
        none.textContent = 'Nothing sent yet.';
        list.append(none);
        return;
      }
      for (const row of rows) {
        const item = document.createElement('div');
        item.className = 'dispatch-item';
        item.innerHTML = `
          <div class="d-head">
            <span class="d-kind">${escape(row.kind_label)}</span>
            <span class="tag ${row.automatic ? '' : 'manual'}">${row.automatic ? 'Automatic' : escape(row.raised_by)}</span>
          </div>
          <div class="d-meta">
            ${[row.vendor, etaLabel(row) !== '—' ? etaLabel(row) : '']
              .filter(Boolean).map(escape).join(' · ')}
            ${row.vendor_phone ? ` · <a class="tel" href="tel:${escape(row.vendor_phone)}">${escape(row.vendor_phone)}</a>` : ''}
          </div>
          ${row.notes ? `<div class="d-note">${escape(row.notes)}</div>` : ''}
          <div class="state">
            ${['requested', 'en_route', 'arrived', 'cancelled']
              .map((state) =>
                `<button data-status="${state}" class="${row.status === state ? 'on' : ''}">${state.replace('_', ' ')}</button>`)
              .join('')}
          </div>`;
        item.querySelectorAll('.state button').forEach((button) => {
          button.onclick = async () => {
            const res = await fetch(CFG.dispatchStatusUrl.replace('__ID__', row.id), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
              body: JSON.stringify({ status: button.dataset.status }),
            });
            if (!res.ok) return;
            const { dispatch } = await res.json();
            Object.assign(row, dispatch);
            paint(rows);
          };
        });
        list.append(item);
      }
    };

    const rows = [...(claim.dispatches || [])];
    paint(rows);

    // --- the form ---
    const form = document.createElement('form');
    form.className = 'dispatch-form';
    form.innerHTML = `
      <select name="kind" aria-label="What to send">
        ${KINDS.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}
      </select>
      <select name="vendor" aria-label="Vendor">
        <option value="">Suggested vendor</option>
      </select>
      <input name="eta_minutes" type="number" min="0" max="600" placeholder="ETA min">
      <input name="notes" placeholder="Notes (optional)" autocomplete="off">
      <button class="tool-btn" type="submit">Dispatch</button>
      <p class="form-error" hidden></p>`;
    const vendorSelect = form.querySelector('[name=vendor]');
    const kindSelect = form.querySelector('[name=kind]');
    const paintVendors = () => {
      const kind = kindSelect.value;
      const options = vendorBook.filter((v) => v.kind === kind);
      vendorSelect.innerHTML = '<option value="">Suggested vendor</option>'
        + options.map((v) => `<option value="${escape(v.name)}" data-phone="${escape(v.phone)}">${escape(v.name)}</option>`).join('');
    };
    kindSelect.onchange = paintVendors;
    paintVendors();

    const error = form.querySelector('.form-error');
    form.onsubmit = async (event) => {
      event.preventDefault();
      error.hidden = true;
      const data = Object.fromEntries(new FormData(form));
      const picked = vendorSelect.selectedOptions[0];
      if (picked && picked.dataset.phone) data.vendor_phone = picked.dataset.phone;
      const button = form.querySelector('button');
      button.disabled = true;
      try {
        const res = await fetch(CFG.createDispatchUrl.replace('__ID__', claim.id), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
          body: JSON.stringify(data),
        });
        const body = await res.json();
        if (!res.ok) {
          error.textContent = body.error || 'Could not dispatch that.';
          error.hidden = false;
          return;
        }
        rows.unshift(body.dispatch);
        paint(rows);
        form.reset();
        // The claim itself may have moved to dispatched.
        const index = claims.findIndex((c) => c.id === body.claim.id);
        if (index > -1) claims[index] = body.claim;
        render();
      } catch (exception) {
        error.textContent = 'Could not reach the server.';
        error.hidden = false;
      } finally {
        button.disabled = false;
      }
    };
    wrap.append(form);
    return wrap;
  }

  async function openConversation(id) {
    openDrawer('Call #' + id);
    const body = $('drawer-body');
    body.innerHTML = '<p class="empty">Loading the transcript…</p>';
    try {
      const res = await fetch(CFG.conversationDetailUrl.replace('__ID__', id));
      const call = await res.json();
      body.replaceChildren();

      const who = call.policyholder ? call.policyholder.full_name : 'Unidentified caller';
      body.append(
        detailRows([
          ['Caller', escape(who)],
          ['Verified', call.verified ? 'Yes' : 'No'],
          ['Channel', escape(call.channel_label)],
          ['Number', escape(call.caller_number || '—')],
          ['Policy', call.policyholder ? escape(call.policyholder.policy_number) : '—'],
          ['Vehicle', call.policyholder ? escape(call.policyholder.vehicle_line) : '—'],
          ['Started', new Date(call.started_at).toLocaleString()],
          ['Length', clock(call.duration_seconds)],
          ['Ivy ended', escape((call.end_reason || '').replace(/_/g, ' ') || '—')],
          ['Transport', escape((call.close_reason || '').replace(/_/g, ' ') || '—')],
          ['Claim', call.claim_reference ? escape(call.claim_reference) : '—'],
        ])
      );

      if ((call.verifications || []).length) {
        const checks = document.createElement('div');
        checks.className = 'checks verify-timeline';
        checks.innerHTML = call.verifications
          .map((v) =>
            `<span class="tag ${v.passed ? 'verified' : 'unverified'}">${escape(v.policy_number)} ${v.passed ? 'passed' : 'failed'}${v.at ? ' · ' + escape(new Date(v.at).toLocaleTimeString()) : ''}</span>`
          )
          .join('');
        body.append(checks);
      }

      // The player, and the transcript wired to it: every line carries the
      // second it was said, so clicking one seeks there and the line that is
      // currently playing highlights itself.
      let audio = null;
      if (call.recording_url) {
        const player = document.createElement('div');
        player.className = 'player';
        player.innerHTML = `
          <audio controls preload="metadata" src="${escape(call.recording_url)}"></audio>
          <div class="player-note">
            Caller on the left channel, Ivy on the right ·
            ${(call.recording_bytes / 1048576).toFixed(1)} MB ·
            <a href="${escape(call.recording_url)}" download>download</a>
          </div>`;
        body.append(player);
        audio = player.querySelector('audio');
      }

      const heading = document.createElement('div');
      heading.className = 'k mono';
      heading.style.margin = '22px 0 10px';
      heading.textContent = `Transcript · ${(call.turns || []).length} turns` +
        (audio ? ' · click a line to jump there' : '');
      body.append(heading);

      if (!(call.turns || []).length) {
        const none = document.createElement('p');
        none.className = 'empty';
        none.textContent =
          'No transcript. Phone calls have no browser to record one, so only the tool calls are kept.';
        body.append(none);
      }

      const script = document.createElement('div');
      script.className = 'script' + (audio ? ' seekable' : '');
      script.innerHTML = (call.turns || [])
        .map(
          (turn, index) => `
            <div class="turn ${escape(turn.role)}" data-at="${Number(turn.at) || 0}" data-index="${index}">
              <span class="at mono">${escape(Number(turn.at || 0).toFixed(1))}s</span>
              <span class="role mono">${escape(turn.role === 'agent' ? 'Ivy' : turn.role)}</span>
              <span class="said">${escape(turn.text)}</span>
            </div>`
        )
        .join('');
      body.append(script);

      if (audio) {
        const lines = [...script.querySelectorAll('.turn')];
        lines.forEach((line) => {
          line.onclick = () => {
            // A turn is stamped when it was finalised, which is a beat after
            // it started being said. Rewind slightly so the line is not
            // already half over when playback begins.
            audio.currentTime = Math.max(0, Number(line.dataset.at) - 1.5);
            audio.play();
          };
        });
        let lit = null;
        audio.addEventListener('timeupdate', () => {
          const now = audio.currentTime + 1.5;
          let current = null;
          for (const line of lines) {
            if (Number(line.dataset.at) <= now) current = line;
            else break;
          }
          if (current === lit) return;
          if (lit) lit.classList.remove('playing');
          if (current) current.classList.add('playing');
          lit = current;
        });
        // Leaving the drawer must not leave audio playing behind it.
        drawerAudio = audio;
      }

      if ((call.tool_calls || []).length) {
        const missing = {
          is_drivable: 'Filed without asking if the car drives',
          policy_number: 'Filed without a policy number',
          location: 'Filed without a location',
          incident_type: 'Filed with an incident it could not categorise',
        };
        const tools = document.createElement('div');
        tools.className = 'k mono';
        tools.style.margin = '22px 0 10px';
        tools.textContent = 'Tool calls';
        body.append(tools);
        const list = document.createElement('div');
        list.className = 'checks verify-timeline';
        list.innerHTML = call.tool_calls.map((item) => {
          if (item.rejected) {
            return `<span class="tag unverified">Rejected — ${escape(missing[item.missing] || item.missing || 'incomplete')}</span>`;
          }
          return `<span class="tag verified">${escape(item.name)}${item.claim_id ? ' · CV-' + String(item.claim_id).padStart(5, '0') : ''}</span>`;
        }).join('');
        body.append(list);
      }
    } catch (error) {
      body.innerHTML = '<p class="empty">Could not load that call.</p>';
    }
  }

  // --- rendering ------------------------------------------------------------

  function render() {
    if (view === 'claims') {
      const visible = claims.filter(matches);
      $('rows').replaceChildren(...visible.map(claimRow));
      $('board-empty').hidden = claims.length > 0;
      $('row-count').textContent = claims.length
        ? `${visible.length} of ${claims.length} claims`
        : '';
    } else if (view === 'conversations') {
      const visible = conversations.filter(convMatches);
      $('conv-rows').replaceChildren(...visible.map(convRow));
      $('conv-empty').hidden = conversations.length > 0;
      $('conv-count').textContent = conversations.length
        ? `${visible.length} of ${conversations.length} calls`
        : '';
    } else if (view === 'dispatches') {
      const visible = dispatches.filter(dispatchMatches);
      $('dispatch-rows').replaceChildren(...visible.map(dispatchRow));
      $('dispatch-empty').hidden = dispatches.length > 0;
      $('dispatch-count').textContent = dispatches.length
        ? `${visible.length} of ${dispatches.length} on the board`
        : '';
    } else if (view === 'map') {
      paintMap();
    }
    paintHandoffBanner();
  }

  function applyStats(stats) {
    $('stat-total').textContent = stats.today != null ? stats.today : stats.total;
    $('stat-critical').textContent = stats.critical;
    $('stat-tows').textContent = stats.tows;
    if ($('stat-handoffs')) $('stat-handoffs').textContent = stats.handoffs || 0;
    $('stat-avg').textContent = stats.avg_risk;
  }

  function paintHandoffBanner() {
    const banner = $('handoff-banner');
    if (!banner) return;
    const waitingClaims = claims.filter((claim) => claim.needs_human);
    const waitingCalls = conversations.filter((call) => call.needs_human && !call.claim_id);
    const total = waitingClaims.length + waitingCalls.length;
    banner.hidden = total === 0;
    if (!total) return;
    if (waitingClaims.length && !waitingCalls.length) {
      $('handoff-text').textContent = waitingClaims.length === 1
        ? `${reference(waitingClaims[0].id)} is waiting for a person.`
        : `${waitingClaims.length} claims are waiting for a person.`;
    } else if (waitingCalls.length && !waitingClaims.length) {
      $('handoff-text').textContent = waitingCalls.length === 1
        ? 'A live call is waiting for a person.'
        : `${waitingCalls.length} calls are waiting for a person.`;
    } else {
      $('handoff-text').textContent = `${total} handoffs are waiting for a person.`;
    }
  }

  let incidentMap = null;
  let mapMarkers = [];

  function paintMap() {
    const el = $('incident-map');
    if (!el || !window.L) return;
    const pinned = claims.filter((claim) => claim.lat != null && claim.lng != null && matches(claim));
    if (!incidentMap) {
      incidentMap = L.map(el).setView([10.5, -61.3], 8);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap',
      }).addTo(incidentMap);
    }
    mapMarkers.forEach((marker) => marker.remove());
    mapMarkers = [];
    const colors = { critical: '#f87171', high: '#fb923c', medium: '#fbbf24', low: '#4ade80' };
    const bounds = [];
    for (const claim of pinned) {
      const color = colors[claim.priority] || '#6366f1';
      const marker = L.circleMarker([claim.lat, claim.lng], {
        radius: 8,
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      }).addTo(incidentMap);
      marker.bindPopup(
        `${escape(reference(claim.id))} · risk ${escape(claim.risk_score)}<br>${escape(claim.location)}`
      );
      marker.on('click', () => openClaim(claim));
      mapMarkers.push(marker);
      bounds.push([claim.lat, claim.lng]);
    }
    const openTows = dispatches.filter((row) =>
      row.kind === 'tow' && row.status !== 'arrived' && row.status !== 'cancelled'
    );
    let towPins = 0;
    for (const row of openTows) {
      const claim = claims.find((c) => c.id === row.claim_id);
      const lat = row.lat != null ? row.lat : claim && claim.lat;
      const lng = row.lng != null ? row.lng : claim && claim.lng;
      if (lat == null || lng == null) continue;
      const marker = L.circleMarker([Number(lat) + 0.003, Number(lng) + 0.003], {
        radius: 6,
        color: '#38bdf8',
        fillColor: '#38bdf8',
        fillOpacity: 0.95,
        weight: 2,
      }).addTo(incidentMap);
      marker.bindPopup(
        `${escape(row.vendor || 'Tow')} · ${escape(row.claim_reference || '')}<br>`
        + `${row.eta_minutes != null ? escape(row.eta_minutes) + 'm' : 'en route'}`
      );
      marker.on('click', () => {
        if (claim) openClaim(claim);
      });
      mapMarkers.push(marker);
      bounds.push([Number(lat) + 0.003, Number(lng) + 0.003]);
      towPins += 1;
    }
    if (bounds.length) incidentMap.fitBounds(bounds, { padding: [28, 28], maxZoom: 13 });
    if ($('map-empty')) $('map-empty').hidden = pinned.length > 0 || towPins > 0;
    if ($('map-count')) {
      const bits = [];
      if (pinned.length) bits.push(`${pinned.length} pinned`);
      if (towPins) bits.push(`${towPins} open tow${towPins === 1 ? '' : 's'}`);
      $('map-count').textContent = bits.join(' · ');
    }
    const unpinned = claims.filter((claim) =>
      (claim.lat == null || claim.lng == null) && matches(claim)
    );
    const pinBtn = $('map-unpinned');
    if (pinBtn) {
      pinBtn.textContent = `Unpinned · ${unpinned.length}`;
      pinBtn.classList.toggle('on', showUnpinned);
    }
    const list = $('unpinned-list');
    if (list) {
      list.hidden = !showUnpinned;
      if (showUnpinned) {
        list.innerHTML = unpinned.length
          ? unpinned.map((claim) =>
              `<a href="#claim-${claim.id}">${escape(reference(claim.id))} · ${escape(claim.location || 'no location')}</a>`
            ).join('')
          : '<p class="empty">Every visible claim is pinned.</p>';
      }
    }
    setTimeout(() => incidentMap.invalidateSize(), 80);
  }

  function csvCell(value) {
    const text = String(value == null ? '' : value);
    if (/[",\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }

  function exportCsv() {
    const rows = claims.filter(matches);
    const cols = [
      'id', 'reference', 'policy_number', 'status', 'priority', 'risk_score',
      'location', 'caller_name', 'vehicle', 'assigned_to', 'sms_status',
      'email_status', 'created_at',
    ];
    const lines = [cols.join(',')];
    for (const claim of rows) {
      lines.push([
        claim.id,
        reference(claim.id),
        claim.policy_number,
        claim.status,
        claim.priority,
        claim.risk_score,
        claim.location,
        claim.policyholder_name || claim.caller_name,
        claim.vehicle,
        claim.assigned_to,
        claim.sms_status,
        claim.email_status,
        claim.created_at,
      ].map(csvCell).join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'claims.csv';
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function setStatus(id, status) {
    try {
      const res = await fetch(CFG.statusUrl.replace('__ID__', id), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) return;
      const { claim } = await res.json();
      const index = claims.findIndex((c) => c.id === claim.id);
      if (index > -1) claims[index] = claim;
      render();
    } catch (error) {
      /* a failed status flip is not worth interrupting the board for */
    }
  }

  async function setDispatchStatus(id, status) {
    try {
      const res = await fetch(CFG.dispatchStatusUrl.replace('__ID__', id), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) return;
      const { dispatch } = await res.json();
      const index = dispatches.findIndex((row) => row.id === dispatch.id);
      if (index > -1) dispatches[index] = { ...dispatches[index], ...dispatch };
      render();
    } catch (error) {
      /* same as claim status: the next poll will catch up */
    }
  }

  function showView(name) {
    view = name;
    document.querySelectorAll('.tab-btn').forEach((t) => t.classList.toggle('on', t.dataset.view === name));
    $('view-claims').hidden = name !== 'claims';
    $('view-conversations').hidden = name !== 'conversations';
    if ($('view-dispatches')) $('view-dispatches').hidden = name !== 'dispatches';
    if ($('view-map')) $('view-map').hidden = name !== 'map';
    render();
  }

  function applyHash() {
    const raw = (location.hash || '').replace(/^#/, '');
    if (!raw) return;
    if (raw.startsWith('call-')) {
      const id = Number(raw.slice(5));
      if (id) {
        showView('conversations');
        openConversation(id);
      }
      return;
    }
    if (raw.startsWith('claim-')) {
      const id = Number(raw.slice(6));
      const claim = claims.find((c) => c.id === id);
      showView('claims');
      if (claim) openClaim(claim);
      return;
    }
    if (raw === 'dispatches' || raw.startsWith('dispatch-')) {
      showView('dispatches');
      return;
    }
    if (raw === 'map') {
      showView('map');
      return;
    }
    if (raw.startsWith('policy-')) {
      const code = raw.slice(7);
      if ($('search')) $('search').value = code;
      term = code;
      showView('claims');
    }
  }

  async function poll() {
    try {
      const url = latestId ? `${CFG.feedUrl}?since=${latestId}` : CFG.feedUrl;
      const [feed, convs, fleet] = await Promise.all([
        fetch(url).then((res) => res.json()),
        fetch(CFG.conversationsUrl).then((res) => res.json()),
        CFG.dispatchUrl
          ? fetch(CFG.dispatchUrl).then((res) => res.json()).catch(() => ({ dispatches: [] }))
          : Promise.resolve({ dispatches: [] }),
      ]);

      applyStats(feed.stats);
      $('last-update').textContent = new Date().toLocaleTimeString();
      conversations = convs.conversations;
      dispatches = fleet.dispatches || [];

      if (feed.incremental) {
        if (feed.claims.length) {
          feed.claims.forEach((claim) => fresh.add(claim.id));
          claims = feed.claims.concat(claims);
          setTimeout(() => { feed.claims.forEach((c) => fresh.delete(c.id)); }, 1600);
          announce(feed.claims.length, feed.claims);
        }
      } else {
        claims = feed.claims;
      }
      latestId = Math.max(latestId, feed.latest_id || 0);
      render();
      if (!hashReady) {
        hashReady = true;
        applyHash();
      }
    } catch (error) {
      $('last-update').textContent = 'reconnecting…';
    } finally {
      setTimeout(poll, POLL_MS);
    }
  }

  // A claim arriving mid-demo should be obvious even if the reader is looking
  // at the voice tab in the next window.
  function chime() {
    if (localStorage.getItem('cv-mute') === '1') return;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.35);
    } catch (error) { /* no audio context */ }
  }

  function announce(count, incoming) {
    const original = 'Dispatcher · ClaimVoice';
    document.title = `(${count}) New claim · ClaimVoice`;
    setTimeout(() => { document.title = original; }, 4000);
    chime();
    const toast = $('claim-toast');
    if (!toast) return;
    const first = (incoming || [])[0];
    if (!first) return;
    const kinds = (first.dispatches || []).map((row) => row.kind_label).join(' · ');
    toast.hidden = false;
    toast.textContent = `${reference(first.id)} filed · risk ${first.risk_score}`
      + (kinds ? ` · ${kinds}` : '');
    clearTimeout(toast._hide);
    toast._hide = setTimeout(() => { toast.hidden = true; }, 5000);
  }

  document.querySelectorAll('.tab-btn').forEach((tab) => {
    tab.onclick = () => showView(tab.dataset.view);
  });

  document.querySelectorAll('[data-dfilter]').forEach((chip) => {
    chip.onclick = () => {
      dispatchFilter = chip.dataset.dfilter;
      document.querySelectorAll('[data-dfilter]').forEach((c) => c.classList.toggle('on', c === chip));
      render();
    };
  });

  window.addEventListener('hashchange', applyHash);

  document.querySelectorAll('[data-filter]').forEach((chip) => {
    chip.onclick = () => {
      filter = chip.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach((c) => c.classList.toggle('on', c === chip));
      render();
    };
  });

  document.querySelectorAll('[data-cfilter]').forEach((chip) => {
    chip.onclick = () => {
      convFilter = chip.dataset.cfilter;
      document.querySelectorAll('[data-cfilter]').forEach((c) => c.classList.toggle('on', c === chip));
      render();
    };
  });

  if ($('handoff-jump')) {
    $('handoff-jump').onclick = () => {
      const waitingCalls = conversations.filter((call) => call.needs_human && !call.claim_id);
      const waitingClaims = claims.filter((claim) => claim.needs_human);
      if (waitingCalls.length && !waitingClaims.length) {
        convFilter = 'handoff';
        document.querySelectorAll('[data-cfilter]').forEach((c) => c.classList.toggle('on', c.dataset.cfilter === 'handoff'));
        showView('conversations');
        return;
      }
      filter = 'handoff';
      document.querySelectorAll('[data-filter]').forEach((c) => c.classList.toggle('on', c.dataset.filter === 'handoff'));
      showView('claims');
    };
  }

  $('search').addEventListener('input', (event) => {
    term = event.target.value.trim().toLowerCase();
    render();
  });

  const sound = $('sound-toggle');
  if (sound) {
    const muted = localStorage.getItem('cv-mute') === '1';
    sound.classList.toggle('on', !muted);
    sound.textContent = muted ? 'Sound off' : 'Sound on';
    sound.setAttribute('aria-pressed', muted ? 'false' : 'true');
    sound.onclick = () => {
      const next = localStorage.getItem('cv-mute') === '1' ? '0' : '1';
      localStorage.setItem('cv-mute', next);
      sound.classList.toggle('on', next !== '1');
      sound.textContent = next === '1' ? 'Sound off' : 'Sound on';
      sound.setAttribute('aria-pressed', next === '1' ? 'false' : 'true');
    };
  }

  document.addEventListener('keydown', (event) => {
    if (event.target.matches('input, textarea, select, [contenteditable]')) return;
    if (event.key === '/') {
      event.preventDefault();
      $('search').focus();
    }
    if (event.key === 'm') showView('map');
    if (event.key === 'd') showView('dispatches');
    if (event.key === 'c') showView('claims');
  });

  // Relative timestamps go stale on a board left open during a demo.
  setInterval(() => {
    if (claims.length || conversations.length || dispatches.length) render();
  }, 30000);

  if (CFG.vendorsUrl) {
    fetch(CFG.vendorsUrl)
      .then((res) => res.json())
      .then((data) => { vendorBook = data.vendors || []; })
      .catch(() => { vendorBook = []; });
  }

  const arriveDue = $('arrive-due');
  if (arriveDue) {
    arriveDue.onclick = async () => {
      const due = dispatches.filter((row) =>
        row.status === 'en_route' && remainingMinutes(row) === 0
      );
      for (const row of due) {
        await setDispatchStatus(row.id, 'arrived');
      }
    };
  }

  const exportBtn = $('export-csv');
  if (exportBtn) exportBtn.onclick = exportCsv;

  const unpinnedBtn = $('map-unpinned');
  if (unpinnedBtn) {
    unpinnedBtn.onclick = () => {
      showUnpinned = !showUnpinned;
      paintMap();
    };
  }

  const inject = $('inject-demo');
  if (inject && CFG.demoUrl) {
    inject.onclick = async () => {
      inject.disabled = true;
      try {
        const res = await fetch(CFG.demoUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': CFG.csrfToken, Accept: 'application/json' },
        });
        const body = await res.json();
        if (body.claim) {
          if (!claims.some((c) => c.id === body.claim.id)) {
            claims.unshift(body.claim);
            fresh.add(body.claim.id);
            latestId = Math.max(latestId, body.claim.id);
          }
          announce(1, [body.claim]);
          render();
          openClaim(body.claim);
        }
      } finally {
        inject.disabled = false;
      }
    };
  }

  poll();
})();
