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

  let claims = [];
  let conversations = [];
  let latestId = 0;
  let filter = 'all';
  let convFilter = 'all';
  let view = 'claims';
  let term = '';
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
    if (term && !`${claim.policy_number} ${claim.location} ${claim.caller_name} ${claim.vehicle}`
      .toLowerCase().includes(term)) return false;
    if (filter === 'all') return true;
    if (filter === 'critical') return claim.priority === 'critical';
    if (filter === 'unverified') return !claim.verified;
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
      </td>
      <td><span class="tag">${escape(claim.incident_label)}</span></td>
      <td>
        <div class="where">${escape(claim.location)}</div>
        <div class="who-line">${[claim.policyholder_name || claim.caller_name, claim.vehicle].filter(Boolean).map(escape).join(' · ')}</div>
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
      ])
    );
    if (claim.description) {
      const note = document.createElement('p');
      note.className = 'drawer-note';
      note.textContent = claim.description;
      body.append(note);
    }
    if (claim.conversation_id) {
      const link = document.createElement('button');
      link.className = 'btn small';
      link.textContent = 'Open the call';
      link.onclick = () => openConversation(claim.conversation_id);
      body.append(link);
    }
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
          ['Ended', escape(call.close_reason || '—')],
          ['Claim', call.claim_reference ? escape(call.claim_reference) : '—'],
        ])
      );

      if ((call.verifications || []).length) {
        const checks = document.createElement('div');
        checks.className = 'checks';
        checks.innerHTML = call.verifications
          .map((v) =>
            `<span class="tag ${v.passed ? 'verified' : 'unverified'}">${escape(v.policy_number)} ${v.passed ? 'passed' : 'failed'}</span>`
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
        const tools = document.createElement('div');
        tools.className = 'k mono';
        tools.style.margin = '22px 0 10px';
        tools.textContent = 'Tool calls';
        const pre = document.createElement('pre');
        pre.className = 'tool-json';
        pre.textContent = JSON.stringify(call.tool_calls, null, 2);
        body.append(tools, pre);
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
    } else {
      const visible = conversations.filter(convMatches);
      $('conv-rows').replaceChildren(...visible.map(convRow));
      $('conv-empty').hidden = conversations.length > 0;
      $('conv-count').textContent = conversations.length
        ? `${visible.length} of ${conversations.length} calls`
        : '';
    }
  }

  function applyStats(stats) {
    $('stat-total').textContent = stats.total;
    $('stat-critical').textContent = stats.critical;
    $('stat-tows').textContent = stats.tows;
    $('stat-avg').textContent = stats.avg_risk;
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

  async function poll() {
    try {
      const url = latestId ? `${CFG.feedUrl}?since=${latestId}` : CFG.feedUrl;
      const [feed, convs] = await Promise.all([
        fetch(url).then((res) => res.json()),
        fetch(CFG.conversationsUrl).then((res) => res.json()),
      ]);

      applyStats(feed.stats);
      $('last-update').textContent = new Date().toLocaleTimeString();
      conversations = convs.conversations;

      if (feed.incremental) {
        if (feed.claims.length) {
          feed.claims.forEach((claim) => fresh.add(claim.id));
          claims = feed.claims.concat(claims);
          setTimeout(() => { feed.claims.forEach((c) => fresh.delete(c.id)); }, 1600);
          announce(feed.claims.length);
        }
      } else {
        claims = feed.claims;
      }
      latestId = Math.max(latestId, feed.latest_id || 0);
      render();
    } catch (error) {
      $('last-update').textContent = 'reconnecting…';
    } finally {
      setTimeout(poll, POLL_MS);
    }
  }

  // A claim arriving mid-demo should be obvious even if the reader is looking
  // at the voice tab in the next window.
  function announce(count) {
    const original = 'Dispatcher · ClaimVoice';
    document.title = `(${count}) New claim · ClaimVoice`;
    setTimeout(() => { document.title = original; }, 4000);
  }

  document.querySelectorAll('.tab-btn').forEach((tab) => {
    tab.onclick = () => {
      view = tab.dataset.view;
      document.querySelectorAll('.tab-btn').forEach((t) => t.classList.toggle('on', t === tab));
      $('view-claims').hidden = view !== 'claims';
      $('view-conversations').hidden = view !== 'conversations';
      render();
    };
  });

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

  $('search').addEventListener('input', (event) => {
    term = event.target.value.trim().toLowerCase();
    render();
  });

  // Relative timestamps go stale on a board left open during a demo.
  setInterval(() => {
    if (claims.length || conversations.length) render();
  }, 30000);

  poll();
})();
