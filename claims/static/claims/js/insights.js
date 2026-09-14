/* The insights page.
 *
 * Every comparison chart here is a labelled horizontal bar: the categories
 * have long names, and the value sits on the row so nothing depends on colour
 * alone. The line chart is the one exception — it is time, not categories.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const CFG = window.CLAIMVOICE;

  const ORDINAL = ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab'];
  const CATEGORICAL = ['#3987e5', '#d95926', '#199e70'];
  const SINGLE = '#3987e5';

  const escape = (value) =>
    String(value == null ? '' : value).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  const clock = (seconds) => {
    const whole = Math.round(seconds || 0);
    return whole >= 60
      ? `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
      : `${whole}s`;
  };

  const signed = (value, suffix = '') => {
    if (!value) return `same as last window`;
    const prefix = value > 0 ? '+' : '';
    return `${prefix}${value}${suffix} vs last window`;
  };

  /* rows: [{label, count, colour, note}] — scaled against the largest row. */
  function drawBars(node, rows, options = {}) {
    const max = Math.max(1, ...rows.map((row) => row.count));
    const total = options.shareOf ?? rows.reduce((sum, row) => sum + row.count, 0);
    node.replaceChildren();

    if (!rows.some((row) => row.count)) {
      node.innerHTML = '<p class="empty">Nothing in this window yet.</p>';
      return;
    }

    for (const row of rows) {
      const share = row.share != null
        ? row.share
        : (total ? Math.round((100 * row.count) / total) : 0);
      const el = document.createElement('div');
      el.className = 'bar-row';
      el.title = `${row.label}: ${row.count}${total ? ` of ${total} (${share}%)` : ''}`;
      el.innerHTML = `
        <span class="bar-label">${escape(row.label)}</span>
        <span class="bar-track">
          <span class="bar-fill" style="width:${(100 * row.count) / max}%;background:${row.colour}"></span>
        </span>
        <span class="bar-value">${escape(row.count)}</span>
        <span class="bar-share">${row.note || (total && row.count ? share + '%' : '')}</span>`;
      node.append(el);
    }
  }

  function paint(data) {
    const head = data.headline;
    const deltas = data.deltas || {};
    $('kpi-calls').textContent = head.calls;
    $('kpi-calls-sub').textContent = signed(deltas.calls);
    $('kpi-verified').textContent = head.verified_rate + '%';
    $('kpi-verified-sub').textContent = `${data.funnel[1].count} of ${head.calls} calls · ${signed(deltas.verified_rate, 'pp')}`;
    $('kpi-complete').textContent = head.completion_rate + '%';
    $('kpi-complete-sub').textContent = `${head.claims} claims filed · ${signed(deltas.completion_rate, 'pp')}`;
    $('kpi-median').textContent = clock(head.median_seconds);
    $('kpi-median-sub').textContent = signed(deltas.median_seconds, 's');
    $('kpi-p90').textContent = clock(head.p90_seconds);
    $('kpi-dropoff').textContent = head.dropoff_after_verify;
    $('kpi-tows').textContent = head.tows;
    $('kpi-tows-sub').textContent = head.claims
      ? `${Math.round((100 * head.tows) / head.claims)}% of claims`
      : 'No claims yet';
    $('kpi-recordings').textContent = head.recordings;

    const windowLabel = data.window_days === 1
      ? 'Last 24 hours'
      : data.window_days >= 365
        ? 'All recorded calls'
        : `Last ${data.window_days} days`;
    const generated = data.generated_at
      ? new Date(data.generated_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
      : '';
    $('window-meta').textContent = generated
      ? `${windowLabel} · compared with the ${data.window_days === 365 ? 'prior stretch' : 'previous window'} · updated ${generated}`
      : windowLabel;

    drawBars(
      $('funnel'),
      data.funnel.map((stage, index) => ({
        label: stage.label,
        count: stage.count,
        colour: ORDINAL[index] || ORDINAL[ORDINAL.length - 1],
        note: stage.from_previous == null ? '' : `${stage.from_previous}% of previous`,
      })),
      { shareOf: data.funnel[0].count }
    );

    drawBars(
      $('risk'),
      data.risk_bands.map((band, index) => ({
        label: band.label,
        count: band.count,
        colour: ORDINAL[index],
      }))
    );

    drawBars(
      $('incidents'),
      data.incident_types.map((type, index) => ({
        label: type.label,
        count: type.count,
        colour: CATEGORICAL[index % CATEGORICAL.length],
      }))
    );

    drawBars(
      $('channels'),
      data.channels.map((channel, index) => ({
        label: channel.label,
        count: channel.count,
        colour: CATEGORICAL[index % CATEGORICAL.length],
      }))
    );

    drawBars(
      $('weekdays'),
      data.weekdays.map((row, index) => ({
        label: row.label,
        count: row.count,
        colour: ORDINAL[Math.min(index, ORDINAL.length - 1)],
      }))
    );

    drawBars(
      $('durations'),
      data.durations.map((row, index) => ({
        label: row.label,
        count: row.count,
        colour: ORDINAL[index] || ORDINAL[ORDINAL.length - 1],
      }))
    );

    drawBars(
      $('locations'),
      data.locations.map((row) => ({ label: row.label, count: row.count, colour: SINGLE }))
    );

    drawBars(
      $('drivable'),
      data.drivable.map((row, index) => ({
        label: row.label,
        count: row.count,
        colour: CATEGORICAL[index % CATEGORICAL.length],
      }))
    );

    drawBars(
      $('injuries'),
      data.injuries.map((row, index) => ({
        label: row.label,
        count: row.count,
        colour: CATEGORICAL[index % CATEGORICAL.length],
      }))
    );

    drawBars(
      $('claim-status'),
      data.claim_status.map((row, index) => ({
        label: row.label,
        count: row.count,
        colour: ORDINAL[index] || ORDINAL[ORDINAL.length - 1],
      }))
    );

    const days = data.timeseries.map((row) => {
      const date = new Date(row.date + 'T00:00:00');
      return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    });
    Charts.line($('over-time'), [
      {
        name: 'Calls answered',
        colour: CATEGORICAL[0],
        points: data.timeseries.map((row, i) => ({ x: i, y: row.calls, label: days[i] })),
      },
      {
        name: 'Identity verified',
        colour: CATEGORICAL[2],
        points: data.timeseries.map((row, i) => ({ x: i, y: row.verified, label: days[i] })),
      },
      {
        name: 'Claims filed',
        colour: CATEGORICAL[1],
        points: data.timeseries.map((row, i) => ({ x: i, y: row.claims, label: days[i] })),
      },
    ], { label: 'Calls, verified callers and claims per day', height: 220 });

    Charts.columns(
      $('by-hour'),
      data.hours.map((row) => ({
        y: row.count,
        label: `${String(row.hour).padStart(2, '0')}:00`,
        tick: row.hour % 6 === 0 ? String(row.hour).padStart(2, '0') : '',
      })),
      { colour: SINGLE }
    );

    drawBars(
      $('end-reasons'),
      data.end_reasons.map((row) => ({ label: row.label, count: row.count, colour: SINGLE }))
    );

    drawBars(
      $('dispatch-kinds'),
      data.dispatch.by_kind.map((row, index) => ({
        label: row.label,
        count: row.count,
        colour: CATEGORICAL[index % CATEGORICAL.length],
      }))
    );
    $('dispatch-summary').innerHTML = [
      [data.dispatch.open, 'still on their way'],
      [data.dispatch.manual, 'raised by a dispatcher'],
    ]
      .map(
        ([value, label]) =>
          `<div><span class="q-value">${escape(value)}</span><span class="q-label">${escape(label)}</span></div>`
      )
      .join('');

    const dispatchers = data.dispatchers;
    $('dispatcher-summary').innerHTML = [
      [dispatchers.handoffs, 'callers asked for a person'],
      [dispatchers.answered, 'put through to somebody'],
      [dispatchers.no_answer, 'nobody picked up'],
      [`${dispatchers.answer_rate}%`, 'of rings answered'],
    ]
      .map(
        ([value, label]) =>
          `<div><span class="q-value">${escape(value)}</span><span class="q-label">${escape(label)}</span></div>`
      )
      .join('');
    const dispatcherRows = $('dispatchers');
    const dispatcherEmpty = $('dispatchers-empty');
    dispatcherRows.replaceChildren();
    dispatcherEmpty.hidden = dispatchers.people.length > 0;
    for (const person of dispatchers.people) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escape(person.name)}</td>
        <td>${person.on_call ? '<span class="pill ok">On call</span>' : ''}</td>
        <td class="mono">${escape(person.rung)}</td>
        <td class="mono">${escape(person.answered)}</td>
        <td class="mono">${escape(person.answer_rate)}%</td>
        <td class="mono">${person.avg_answer_seconds == null ? '—' : escape(clock(person.avg_answer_seconds))}</td>`;
      dispatcherRows.append(tr);
    }

    const quality = data.quality;
    drawBars(
      $('rejections'),
      quality.rejections.map((row) => ({
        label: row.label,
        count: row.count,
        colour: SINGLE,
      })),
      { shareOf: 0 }
    );
    const rej = $('rejections');
    if (rej && !document.getElementById('rej-fix')) {
      const hint = document.createElement('p');
      hint.className = 'hint';
      hint.id = 'rej-fix';
      hint.innerHTML = '<a href="/settings/#prompt">Open the prompt to fix these</a>';
      rej.after(hint);
    }

    $('quality').innerHTML = [
      [`${quality.rejection_rate}%`, 'of calls needed a second filing attempt'],
      [`${quality.verification_pass_rate}%`, `of ${quality.verification_checks} identity checks passed`],
      [quality.verification_failed, 'identity checks failed'],
      [quality.average_risk, 'average risk score'],
      [quality.unverified_claims, 'claims filed by an unverified caller'],
      [`${quality.agent_ended_rate}%`, 'of calls Ivy ended herself'],
      [quality.median_turns, 'median turns in a call'],
    ]
      .map(
        ([value, label]) =>
          `<div><span class="q-value">${escape(value)}</span><span class="q-label">${escape(label)}</span></div>`
      )
      .join('');

    const body = $('notable');
    const empty = $('notable-empty');
    body.replaceChildren();
    if (!data.notable.length) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    for (const row of data.notable) {
      const when = new Date(row.when);
      const tr = document.createElement('tr');
      tr.className = 'clickable';
      tr.innerHTML = `
        <td class="when">${escape(when.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }))}</td>
        <td>${escape(row.who)}</td>
        <td>${escape(row.channel)}</td>
        <td class="mono">${escape(clock(row.duration))}</td>
        <td>${escape(row.end_reason)}</td>
        <td>${row.flags.map((flag) => `<span class="tag">${escape(flag)}</span>`).join(' ')}</td>`;
      tr.addEventListener('click', () => {
        window.location.href = `${CFG.dashboardUrl}#call-${row.id}`;
      });
      body.append(tr);
    }
  }

  async function load(days) {
    try {
      const res = await fetch(`${CFG.metricsUrl}?days=${days}`);
      paint(await res.json());
      if ($('export')) $('export').href = `${CFG.exportUrl}?days=${days}`;
    } catch (error) {
      $('funnel').innerHTML = '<p class="empty">Could not load metrics.</p>';
    }
  }

  document.querySelectorAll('#range .chip').forEach((chip) => {
    chip.onclick = () => {
      document.querySelectorAll('#range .chip').forEach((c) => c.classList.toggle('on', c === chip));
      load(chip.dataset.days);
    };
  });

  load(30);
  // These numbers only move when a call, a claim, a truck or a vendor call
  // does. Watching those counters means an idle tab costs a heartbeat rather
  // than rebuilding every chart on a fifteen-second timer.
  Live.watch(['claims', 'conversations', 'dispatches', 'vendor_calls'], () => {
    const active = document.querySelector('#range .chip.on');
    return load(active ? active.dataset.days : 30);
  });
})();
