/* The insights page.
 *
 * Every chart here is a labelled horizontal bar, because every question on this
 * page is "compare these magnitudes" and the categories have long names. Values
 * are direct-labelled on every row, so the charts double as the table view and
 * nothing depends on colour alone.
 *
 * Palettes are the validated ones: an ordinal blue ramp for the ordered scales
 * (funnel stages, risk bands) and the first three categorical slots where the
 * categories are identities rather than an order.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const CFG = window.CLAIMVOICE;

  // Ordinal ramp, light -> dark. Validated on this surface: monotone lightness,
  // visible step gaps, light end clears 2:1.
  const ORDINAL = ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab'];
  // Categorical slots 1-3, validated all-pairs on this surface.
  const CATEGORICAL = ['#3987e5', '#d95926', '#199e70'];
  // One hue: these bars are a single series ranked by size.
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

  /* rows: [{label, count, colour, note}] — scaled against the largest row so
   * the bars compare against each other rather than against the container. */
  function drawBars(node, rows, options = {}) {
    const max = Math.max(1, ...rows.map((row) => row.count));
    const total = options.shareOf ?? rows.reduce((sum, row) => sum + row.count, 0);
    node.replaceChildren();

    if (!rows.some((row) => row.count)) {
      node.innerHTML = '<p class="empty">Nothing in this window yet.</p>';
      return;
    }

    for (const row of rows) {
      const share = total ? Math.round((100 * row.count) / total) : 0;
      const el = document.createElement('div');
      el.className = 'bar-row';
      // The tooltip carries the exact figures; the row itself carries the label
      // and value, so the hover is an addition rather than the only way to read it.
      el.title = `${row.label}: ${row.count}${total ? ` of ${total} (${share}%)` : ''}`;
      el.innerHTML = `
        <span class="bar-label">${escape(row.label)}</span>
        <span class="bar-track">
          <span class="bar-fill" style="width:${(100 * row.count) / max}%;background:${row.colour}"></span>
        </span>
        <span class="bar-value">${escape(row.count)}</span>
        <span class="bar-share">${total && row.count ? share + '%' : ''}</span>`;
      node.append(el);
    }
  }

  function paint(data) {
    const head = data.headline;
    $('kpi-calls').textContent = head.calls;
    $('kpi-verified').textContent = head.verified_rate + '%';
    $('kpi-verified-sub').textContent = `${data.funnel[1].count} of ${head.calls} calls`;
    $('kpi-complete').textContent = head.completion_rate + '%';
    $('kpi-complete-sub').textContent = `${head.claims} claims filed`;
    $('kpi-median').textContent = clock(head.median_seconds);

    drawBars(
      $('funnel'),
      data.funnel.map((stage, index) => ({
        label: stage.label,
        count: stage.count,
        colour: ORDINAL[index] || ORDINAL[ORDINAL.length - 1],
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

    $('quality').innerHTML = [
      [`${quality.rejection_rate}%`, 'of calls needed a second filing attempt'],
      [`${quality.verification_pass_rate}%`, `of ${quality.verification_checks} identity checks passed`],
      [quality.average_risk, 'average risk score'],
      [quality.unverified_claims, 'claims filed by an unverified caller'],
    ]
      .map(
        ([value, label]) =>
          `<div><span class="q-value">${escape(value)}</span><span class="q-label">${escape(label)}</span></div>`
      )
      .join('');
  }

  async function load(days) {
    try {
      const res = await fetch(`${CFG.metricsUrl}?days=${days}`);
      paint(await res.json());
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
  // A board left open during a demo should keep up with the calls landing.
  setInterval(() => {
    const active = document.querySelector('#range .chip.on');
    load(active ? active.dataset.days : 30);
  }, 15000);
})();
