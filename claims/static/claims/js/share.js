(() => {
  const escape = (value) =>
    String(value == null ? '' : value).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  function remainingMinutes(row) {
    if (row.eta_minutes == null) return null;
    if (row.status === 'arrived') return 0;
    if (row.status === 'cancelled') return null;
    const elapsed = (Date.now() - new Date(row.created_at).getTime()) / 60000;
    return Math.max(0, Math.round(row.eta_minutes - elapsed));
  }

  function etaText(row) {
    if (row.status === 'arrived') return 'arrived';
    if (row.status === 'cancelled') return '';
    const left = remainingMinutes(row);
    if (left == null) return '';
    if (left === 0) return 'due now';
    return left + ' min left';
  }

  const printBtn = document.getElementById('print-btn');
  if (printBtn) printBtn.onclick = () => window.print();

  const mapEl = document.getElementById('share-map');
  if (mapEl && window.L) {
    const lat = Number(mapEl.dataset.lat), lng = Number(mapEl.dataset.lng);
    const map = L.map(mapEl).setView([lat, lng], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
    }).addTo(map);
    L.marker([lat, lng]).addTo(map).bindPopup(mapEl.dataset.label);
  }

  const pick = document.getElementById('file-pick');
  if (pick) {
    const input = pick.querySelector('input');
    const name = pick.querySelector('.file-name');
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      name.textContent = file ? file.name : 'Choose a photo';
      pick.classList.toggle('has-file', Boolean(file));
    });
  }

  const photoForm = document.getElementById('photo-form');
  const photoError = document.getElementById('photo-error');
  const photos = document.getElementById('photos');
  if (photoForm) {
    photoForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (photoError) photoError.hidden = true;
      const button = photoForm.querySelector('button');
      button.disabled = true;
      try {
        const res = await fetch(photoForm.action, {
          method: 'POST',
          headers: { Accept: 'application/json' },
          body: new FormData(photoForm),
        });
        const body = await res.json();
        if (!res.ok) {
          if (photoError) {
            photoError.textContent = body.error || 'Could not upload that.';
            photoError.hidden = false;
          }
          return;
        }
        const empty = document.getElementById('photos-empty');
        if (empty) empty.remove();
        if (photos && body.photo) {
          const figure = document.createElement('figure');
          figure.innerHTML = `<img src="${escape(body.photo.url)}" alt="${escape(body.photo.caption || 'Claim photo')}">`
            + (body.photo.caption ? `<figcaption>${escape(body.photo.caption)}</figcaption>` : '');
          photos.append(figure);
        }
        photoForm.reset();
        if (pick) {
          pick.classList.remove('has-file');
          const label = pick.querySelector('.file-name');
          if (label) label.textContent = 'Choose a photo';
        }
        const count = document.getElementById('photo-count');
        if (count) count.textContent = String((Number(count.textContent) || 0) + 1);
      } catch (error) {
        if (photoError) {
          photoError.textContent = 'Could not reach the server.';
          photoError.hidden = false;
        }
      } finally {
        button.disabled = false;
      }
    });
  }

  const noteForm = document.getElementById('note-form');
  if (noteForm) {
    noteForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = noteForm.querySelector('button');
      button.disabled = true;
      try {
        const data = new FormData(noteForm);
        const res = await fetch(noteForm.action, {
          method: 'POST',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify({ body: data.get('body'), author: 'caller' }),
        });
        const body = await res.json();
        if (res.ok) {
          const list = document.getElementById('notes');
          if (list && body.note) {
            const item = document.createElement('div');
            item.className = 'note-item';
            item.innerHTML = `<b>${escape(body.note.author)}</b> ${escape(body.note.body)}`;
            list.append(item);
          }
          noteForm.reset();
        }
      } finally {
        button.disabled = false;
      }
    });
  }

  const live = document.getElementById('live-dispatches');
  const photoCount = document.getElementById('photo-count');
  const statusPill = document.querySelector('.share-page .pill');
  const liveLine = document.getElementById('share-live');
  const notifyLine = document.getElementById('share-notify');

  function paintBanner(data) {
    if (!liveLine) return;
    const bits = [];
    if (data.needs_human) bits.push('An adjuster has been requested.');
    if (data.assigned_to) bits.push(`${data.assigned_to} is on it.`);
    liveLine.textContent = bits.join(' ');
    liveLine.hidden = bits.length === 0;
  }

  function paintNotify(data) {
    if (!notifyLine) return;
    const bits = [];
    if (data.sms_status === 'sent') bits.push('Text sent');
    if (data.email_status === 'sent') bits.push('Email sent');
    notifyLine.textContent = bits.join(' · ');
    notifyLine.hidden = bits.length === 0;
  }

  function paintDispatches(rows) {
    if (!live) return;
    live.innerHTML = rows.length
      ? rows.map((row) => `
          <div class="live-item">
            <div class="d-head">
              <span class="d-kind">${escape(row.kind_label)}</span>
              <span class="tag">${escape(row.status_label)}</span>
            </div>
            <div class="d-meta">${escape([row.vendor, etaText(row)].filter(Boolean).join(' · '))}</div>
          </div>`).join('')
      : '<p class="empty">Nothing dispatched yet.</p>';
  }

  async function pollLive() {
    if (!live || !live.dataset.liveUrl) return;
    try {
      const res = await fetch(live.dataset.liveUrl);
      const data = await res.json();
      if (photoCount && data.photo_count != null) photoCount.textContent = data.photo_count;
      if (statusPill && data.status_label) statusPill.textContent = data.status_label;
      paintBanner(data);
      paintNotify(data);
      const risk = document.getElementById('share-risk');
      if (risk && data.risk_factors) risk.textContent = data.risk_factors.join(' · ') || '—';
      paintDispatches(data.dispatches || []);
      const notes = document.getElementById('notes');
      if (notes && data.notes) {
        notes.innerHTML = data.notes.map((note) =>
          `<div class="note-item"><b>${escape(note.author)}</b> ${escape(note.body)}</div>`
        ).join('');
      }
    } catch (error) { /* next tick */ }
    setTimeout(pollLive, 3000);
  }
  if (live) pollLive();
})();
