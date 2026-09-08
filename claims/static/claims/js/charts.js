/* Two chart primitives, drawn as inline SVG. No library: these are the only
 * two forms this dashboard needs, and both are a few dozen lines.
 *
 * The interaction rules they follow: the crosshair finds the X and snaps to
 * the nearest point, so a reader aims at a date rather than at a 2px line; one
 * tooltip lists every series at that X, so the pointer never has to land on a
 * particular line; the value leads and the series name follows, because the
 * reader already knows which series they are looking at and wants the number.
 * Every value is also reachable without hovering, from the axis labels and the
 * table each chart is paired with.
 */
window.Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const make = (name, attrs = {}) => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };

  const niceMax = (value) => {
    if (value <= 5) return Math.max(1, value);
    const magnitude = 10 ** Math.floor(Math.log10(value));
    return Math.ceil(value / magnitude) * magnitude;
  };

  /* series: [{ name, colour, points: [{x, y, label}] }] — x is an index, the
   * label is what the axis and tooltip show. */
  function line(node, series, options = {}) {
    node.replaceChildren();
    const width = node.clientWidth || 560;
    const height = options.height || 190;
    const pad = { top: 12, right: 12, bottom: 26, left: 34 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const count = series[0]?.points.length || 0;
    if (!count) {
      node.innerHTML = '<p class="empty">Nothing in this window yet.</p>';
      return;
    }

    const max = niceMax(Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.y))));
    const xAt = (i) => pad.left + (count === 1 ? plotW / 2 : (plotW * i) / (count - 1));
    const yAt = (value) => pad.top + plotH - (plotH * value) / max;

    const svg = make('svg', {
      viewBox: `0 0 ${width} ${height}`,
      width: '100%',
      height,
      role: 'img',
      'aria-label': options.label || 'chart',
    });

    // Grid and axis stay recessive: they orient the eye, they are not the data.
    for (let step = 0; step <= 2; step++) {
      const value = (max / 2) * step;
      const y = yAt(value);
      svg.append(make('line', {
        x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: 'grid',
      }));
      const text = make('text', { x: pad.left - 8, y: y + 3, class: 'axis y' });
      text.textContent = Math.round(value);
      svg.append(text);
    }

    const ticks = count <= 8 ? count : 5;
    for (let step = 0; step < ticks; step++) {
      const index = Math.round((step * (count - 1)) / Math.max(1, ticks - 1));
      const text = make('text', { x: xAt(index), y: height - 8, class: 'axis x' });
      text.textContent = series[0].points[index].label;
      svg.append(text);
    }

    for (const one of series) {
      const path = one.points
        .map((point, i) => `${i ? 'L' : 'M'}${xAt(i).toFixed(1)},${yAt(point.y).toFixed(1)}`)
        .join(' ');
      if (options.area && series.length === 1) {
        svg.append(make('path', {
          d: `${path} L${xAt(count - 1)},${yAt(0)} L${xAt(0)},${yAt(0)} Z`,
          fill: one.colour,
          'fill-opacity': 0.12,
          stroke: 'none',
        }));
      }
      svg.append(make('path', {
        d: path, fill: 'none', stroke: one.colour, 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      }));
    }

    // --- hover layer ---
    const crosshair = make('line', { class: 'crosshair', y1: pad.top, y2: pad.top + plotH, opacity: 0 });
    svg.append(crosshair);
    const dots = series.map((one) =>
      svg.appendChild(make('circle', { r: 4.5, fill: one.colour, stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0 }))
    );

    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    tip.hidden = true;
    node.append(tip);

    const show = (index) => {
      const x = xAt(index);
      crosshair.setAttribute('x1', x);
      crosshair.setAttribute('x2', x);
      crosshair.setAttribute('opacity', 1);
      series.forEach((one, s) => {
        dots[s].setAttribute('cx', x);
        dots[s].setAttribute('cy', yAt(one.points[index].y));
        dots[s].setAttribute('opacity', 1);
      });

      tip.replaceChildren();
      const head = document.createElement('div');
      head.className = 'tip-head';
      // Labels come from the API, so they are inserted as text, never markup.
      head.textContent = series[0].points[index].label;
      tip.append(head);
      for (const one of series) {
        const row = document.createElement('div');
        row.className = 'tip-row';
        const key = document.createElement('span');
        key.className = 'tip-key';
        key.style.background = one.colour;
        const value = document.createElement('b');
        value.textContent = one.points[index].y;
        const name = document.createElement('span');
        name.className = 'tip-name';
        name.textContent = one.name;
        row.append(key, value, name);
        tip.append(row);
      }
      tip.hidden = false;
      const left = Math.min(Math.max(0, (x / width) * node.clientWidth - 60), node.clientWidth - 130);
      tip.style.left = `${left}px`;
    };

    const hide = () => {
      crosshair.setAttribute('opacity', 0);
      dots.forEach((dot) => dot.setAttribute('opacity', 0));
      tip.hidden = true;
    };

    // The pointer only has to be closest, not on the line.
    const nearest = (event) => {
      const box = svg.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * width;
      const ratio = (x - pad.left) / Math.max(1, plotW);
      return Math.min(count - 1, Math.max(0, Math.round(ratio * (count - 1))));
    };
    svg.addEventListener('pointermove', (event) => show(nearest(event)));
    svg.addEventListener('pointerleave', hide);
    // Keyboard readers get the same readout, one point at a time.
    let focused = 0;
    svg.setAttribute('tabindex', '0');
    svg.addEventListener('focus', () => show(focused));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
      event.preventDefault();
      focused = Math.min(count - 1, Math.max(0, focused + (event.key === 'ArrowRight' ? 1 : -1)));
      show(focused);
    });

    node.prepend(svg);

    if (series.length > 1 && options.legend !== false) {
      const legend = document.createElement('div');
      legend.className = 'chart-legend';
      for (const one of series) {
        const item = document.createElement('span');
        const key = document.createElement('span');
        key.className = 'legend-key';
        key.style.background = one.colour;
        const name = document.createElement('span');
        name.textContent = one.name;
        item.append(key, name);
        legend.append(item);
      }
      node.append(legend);
    }
  }

  /* Columns for a fixed cycle — hour of day. The mark is the hit target. */
  function columns(node, points, options = {}) {
    node.replaceChildren();
    if (!points.some((p) => p.y)) {
      node.innerHTML = '<p class="empty">Nothing in this window yet.</p>';
      return;
    }
    const max = niceMax(Math.max(1, ...points.map((p) => p.y)));
    const row = document.createElement('div');
    row.className = 'columns';
    for (const point of points) {
      const cell = document.createElement('div');
      cell.className = 'column';
      cell.title = `${point.label}: ${point.y}`;
      cell.tabIndex = 0;
      const fill = document.createElement('span');
      fill.style.height = `${Math.max(point.y ? 4 : 1, (100 * point.y) / max)}%`;
      fill.style.background = options.colour || '#3987e5';
      if (!point.y) fill.style.opacity = '.25';
      const tick = document.createElement('em');
      tick.textContent = point.tick || '';
      cell.append(fill, tick);
      row.append(cell);
    }
    node.append(row);
  }

  return { line, columns };
})();
