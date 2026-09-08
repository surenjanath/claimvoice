/* The browser half of the call.
 *
 * Microphone -> 24 kHz PCM16 -> wss://agents.assemblyai.com/v1/ws -> speaker.
 * The API key never reaches this page; /api/token/ mints a 60 second token.
 *
 * Both worklets resample, because a browser may quietly ignore the sample rate
 * an AudioContext asks for.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const CFG = window.CLAIMVOICE;
  const WIRE_RATE = 24000;

  // Scratch buffers are reused: allocating on the audio thread causes glitches.
  const CAPTURE_WORKLET = `
    class CaptureProcessor extends AudioWorkletProcessor {
      constructor() {
        super();
        this._ratio = sampleRate / ${WIRE_RATE};
        this._pos = 0; this._prev = 0; this._src = null; this._out = null;
        this._level = 0; this._since = 0;
      }
      _toPcm(samples, len) {
        const pcm = new Int16Array(len);
        for (let i = 0; i < len; i++) {
          const s = Math.max(-1, Math.min(1, samples[i]));
          pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        return pcm;
      }
      _meter(ch) {
        let peak = 0;
        for (let i = 0; i < ch.length; i++) { const v = Math.abs(ch[i]); if (v > peak) peak = v; }
        this._level = Math.max(peak, this._level * 0.82);
        this._since += ch.length;
        if (this._since >= ${WIRE_RATE} / 20) {
          this._since = 0;
          this.port.postMessage({ level: this._level });
        }
      }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (!ch) return true;
        this._meter(ch);
        if (this._ratio === 1) {
          const pcm = this._toPcm(ch, ch.length);
          this.port.postMessage(pcm.buffer, [pcm.buffer]);
          return true;
        }
        const n = ch.length;
        if (!this._src || this._src.length < n + 1) {
          this._src = new Float32Array(n + 1);
          this._out = new Float32Array(Math.ceil((n + 1) / this._ratio) + 2);
        }
        const src = this._src, out = this._out;
        src[0] = this._prev;
        src.set(ch, 1);
        let outLen = 0, pos = this._pos;
        while (pos < n) {
          const i = Math.floor(pos), frac = pos - i;
          out[outLen++] = src[i] + (src[i + 1] - src[i]) * frac;
          pos += this._ratio;
        }
        this._pos = pos - n;
        this._prev = ch[n - 1];
        if (outLen) {
          const pcm = this._toPcm(out, outLen);
          this.port.postMessage(pcm.buffer, [pcm.buffer]);
        }
        return true;
      }
    }
    registerProcessor('capture', CaptureProcessor);
  `;

  // A ring buffer rather than one AudioBufferSource per chunk, which drifts and
  // clicks under jitter. Posting 'stop' empties it for barge-in.
  const PLAYBACK_WORKLET = `
    class PlaybackProcessor extends AudioWorkletProcessor {
      constructor() {
        super();
        this._ring = new Float32Array(sampleRate * 30);
        this._writePos = 0; this._readPos = 0; this._available = 0;
        this._step = ${WIRE_RATE} / sampleRate;
        this._rsPos = 0; this._rsPrev = 0;
        // After a gap the speaker sits at zero, so interpolating from the
        // pre-gap _rsPrev would click. Reset it instead.
        this._drained = false;
        this._level = 0; this._since = 0;
        this.port.onmessage = (e) => {
          if (e.data === 'stop') {
            this._writePos = this._readPos = this._available = 0;
            this._rsPos = this._rsPrev = 0;
            return;
          }
          const int16 = new Int16Array(e.data);
          // int16[-1] would make _rsPrev NaN, silencing the ring for good.
          if (!int16.length) return;
          if (this._drained) { this._rsPrev = 0; this._rsPos = 0; this._drained = false; }
          if (this._step === 1) {
            for (let i = 0; i < int16.length; i++) this._push(int16[i] / 32768);
            return;
          }
          const n = int16.length;
          let pos = this._rsPos;
          while (pos < n) {
            const i = Math.floor(pos), frac = pos - i;
            const a = i === 0 ? this._rsPrev : int16[i - 1] / 32768;
            const b = int16[i] / 32768;
            this._push(a + (b - a) * frac);
            pos += this._step;
          }
          this._rsPos = pos - n;
          this._rsPrev = int16[n - 1] / 32768;
        };
      }
      _push(v) {
        if (this._available < this._ring.length) {
          this._ring[this._writePos] = v;
          this._writePos = (this._writePos + 1) % this._ring.length;
          this._available++;
        }
      }
      process(inputs, outputs) {
        const output = outputs[0], out = output[0], cap = this._ring.length;
        let peak = 0;
        for (let i = 0; i < out.length; i++) {
          if (this._available > 0) {
            out[i] = this._ring[this._readPos];
            this._readPos = (this._readPos + 1) % cap;
            this._available--;
            const v = Math.abs(out[i]); if (v > peak) peak = v;
          } else { out[i] = 0; this._drained = true; }
        }
        this._level = Math.max(peak, this._level * 0.82);
        this._since += out.length;
        if (this._since >= sampleRate / 20) {
          this._since = 0;
          this.port.postMessage({ level: this._level });
        }
        // Mono source, stereo sink.
        for (let ch = 1; ch < output.length; ch++) output[ch].set(out);
        return true;
      }
    }
    registerProcessor('playback', PlaybackProcessor);
  `;

  const blobUrl = (code) =>
    URL.createObjectURL(new Blob([code], { type: 'application/javascript' }));

  let ws, captureCtx, playbackCtx, playback, mic, callStart, timer;
  // Set on session.ready; audio frames before it are dropped.
  let ready = false;
  // Set when Ivy calls end_call: hang up once her closing line has played.
  let hangingUp = false;
  // The call record. AssemblyAI keeps session metadata but not the words, so
  // the page is the only place the transcript exists — it posts it as it goes.
  let sessionId = null;
  let conversationId = null;
  const lastCall = { claimId: null, reference: '', recordingUrl: '', sharePath: '', notified: false };
  let recorder = null;
  const record = { turns: [], toolCalls: [] };
  let recordDirty = false;
  let recordTimer = null;

  function remember(role, text) {
    if (!text) return;
    record.turns.push({
      role,
      text,
      at: callStart ? Number(((Date.now() - callStart) / 1000).toFixed(1)) : 0,
    });
    recordDirty = true;
  }

  async function pushRecord(final) {
    if (!sessionId || (!recordDirty && !final)) return;
    recordDirty = false;
    try {
      const res = await fetch(CFG.conversationUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          channel: 'browser',
          agent_id: CFG.agentId,
          turns: record.turns,
          tool_calls: record.toolCalls,
          ended: Boolean(final),
          close_reason: final || '',
          duration_seconds: callStart ? (Date.now() - callStart) / 1000 : 0,
        }),
      });
      if (res.ok) {
        const body = await res.json();
        if (body.id) conversationId = body.id;
      }
    } catch (error) {
      // A dropped transcript post must not take the call down with it.
      recordDirty = true;
    }
  }

  // --- microphones ---
  // Labels stay empty until permission is granted, so this runs again after
  // getUserMedia.
  async function listMics() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices
      .filter((d) => d.kind === 'audioinput')
      // Chrome's synthetic entries alias a real device and duplicate it.
      .filter((d) => d.deviceId !== 'default' && d.deviceId !== 'communications');
    const select = $('mic');
    const chosen = select.value;
    select.replaceChildren();
    const auto = document.createElement('option');
    auto.value = '';
    auto.textContent = 'Default microphone';
    select.append(auto);
    inputs.forEach((device, i) => {
      const option = document.createElement('option');
      option.value = device.deviceId;
      option.textContent = device.label || `Microphone ${i + 1}`;
      select.append(option);
    });
    if (chosen && inputs.some((d) => d.deviceId === chosen)) select.value = chosen;
  }
  listMics();
  if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
    navigator.mediaDevices.addEventListener('devicechange', listMics);
  }

  $('call-btn').onclick = () => (ws && ws.readyState <= 1 ? stop() : start());

  // --- side rail tabs ---
  let agentLoaded = false;
  const TABS = ['claim', 'events', 'agent'];
  function showTab(name) {
    for (const tab of TABS) {
      $('tab-' + tab).classList.toggle('on', tab === name);
      $('pane-' + tab).hidden = tab !== name;
    }
    if (name === 'agent' && !agentLoaded) {
      agentLoaded = true;
      fetch(CFG.agentUrl)
        .then((res) => res.json())
        .then((agent) => { $('agent-json').textContent = JSON.stringify(agent, null, 2); })
        .catch(() => { agentLoaded = false; $('agent-json').textContent = 'Could not load the agent.'; });
    }
  }
  TABS.forEach((tab) => ($('tab-' + tab).onclick = () => showTab(tab)));

  // --- the orb reacts to whoever is talking ---
  let micLevel = 0, agentLevel = 0;
  function paintOrb() {
    const level = Math.min(1, Math.max(micLevel, agentLevel * 1.2));
    $('orb').style.transform = `scale(${1 + level * 0.42})`;
  }

  async function addWorklet(ctx, code, name) {
    const url = blobUrl(code);
    try { await ctx.audioWorklet.addModule(url); } finally { URL.revokeObjectURL(url); }
    return new AudioWorkletNode(ctx, name);
  }

  async function start() {
    hideRecap();
    lastCall.claimId = null;
    lastCall.reference = '';
    lastCall.recordingUrl = '';
    lastCall.sharePath = '';
    lastCall.notified = false;
    conversationId = null;
    $('call-btn').disabled = true;
    $('mic').disabled = true;
    setStatus('connecting');

    try {
      const res = await fetch(CFG.tokenUrl);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus('error', body.error || 'could not mint a token, check the API key');
        reset();
        return;
      }
      const { token } = await res.json();

      // Two contexts, created inside the click handler so Safari starts them.
      captureCtx = new AudioContext({ sampleRate: WIRE_RATE });
      playbackCtx = new AudioContext({ sampleRate: WIRE_RATE });
      await Promise.all([captureCtx.resume(), playbackCtx.resume()]);

      playback = await addWorklet(playbackCtx, PLAYBACK_WORKLET, 'playback');
      playback.connect(playbackCtx.destination);
      playback.port.onmessage = ({ data }) => {
        if (data && typeof data.level === 'number') { agentLevel = data.level; paintOrb(); }
      };

      const deviceId = $('mic').value;
      mic = await navigator.mediaDevices.getUserMedia({
        audio: {
          // A preference, not `exact`: an unplugged device falls back.
          ...(deviceId ? { deviceId } : {}),
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      listMics();
      const capture = await addWorklet(captureCtx, CAPTURE_WORKLET, 'capture');
      captureCtx.createMediaStreamSource(mic).connect(capture);

      const url = new URL('wss://agents.assemblyai.com/v1/ws');
      url.searchParams.set('token', token);
      ws = new WebSocket(url);

      // The API takes base64 inside JSON, not binary frames.
      recorder = new window.CallRecorder(WIRE_RATE);

      capture.port.onmessage = ({ data }) => {
        if (data && data.level !== undefined) { micLevel = data.level; paintOrb(); return; }
        if (!ready || ws.readyState !== 1) return;
        // Recorded before the socket check would drop it, so the recording is
        // the call as it happened rather than as it was transmitted.
        recorder.addCaller(new Int16Array(data));
        const bytes = new Uint8Array(data);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 0x8000) {
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        }
        ws.send(JSON.stringify({ type: 'input.audio', audio: btoa(binary) }));
        logEvent('up', 'input.audio');
      };

      // Everything about the agent lives server side; the session just names it.
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'session.update', session: { agent_id: CFG.agentId } }));
        logEvent('up', 'session.update', CFG.agentId);
      };

      ws.onmessage = ({ data }) => handleMessage(JSON.parse(data));
      ws.onclose = () => { setStatus('idle'); reset(); };
      ws.onerror = () => { setStatus('error', 'connection failed'); reset(); };
    } catch (error) {
      setStatus('error', error.message);
      reset();
    }
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case 'session.ready':
        sessionId = msg.session_id || null;
        callStart = Date.now();
        // Registered immediately, then little and often: the webhook has no
        // session id of its own and finds this call by it being the open one,
        // so the row has to exist before Ivy can reach for a tool.
        recordDirty = true;
        pushRecord(false);
        recordTimer = setInterval(() => pushRecord(false), 4000);
        timer = setInterval(tick, 1000);
        tick();
        setStatus('listening');
        document.body.classList.add('live');
        $('call-btn').disabled = false;
        $('call-btn').textContent = 'End call';
        $('call-btn').classList.add('end');
        readyFlag(true);
        logEvent('down', msg.type, msg.session_id);
        break;

      case 'input.speech.started':
        // Barge-in: empty the ring buffer so Ivy stops mid-word, and cut the
        // recording to match — the caller never heard the rest.
        playback && playback.port.postMessage('stop');
        recorder && recorder.truncateAgent(recorder.position);
        setStatus('listening');
        logEvent('down', msg.type);
        break;

      case 'reply.started':
        // Anchor this reply to now on the caller's clock.
        recorder && recorder.alignAgent(recorder.position);
        setStatus('speaking');
        logEvent('down', msg.type);
        break;

      case 'reply.audio': {
        const raw = atob(msg.data);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        // Record before the transfer: postMessage neuters the buffer.
        recorder && recorder.addAgent(new Int16Array(bytes.buffer.slice(0)));
        playback && playback.port.postMessage(bytes.buffer, [bytes.buffer]);
        logEvent('down', msg.type);
        break;
      }

      case 'reply.done':
        setStatus('listening');
        if (msg.status === 'interrupted') {
          playback && playback.port.postMessage('stop');
          recorder && recorder.truncateAgent(recorder.position);
        }
        logEvent('down', msg.type, msg.status);
        // The goodbye has been generated; give the speaker time to actually
        // play it out before cutting the line.
        if (hangingUp) {
          hangingUp = false;
          setStatus('speaking', 'saying goodbye');
          setTimeout(stop, agentAudioRemainingMs() + 400);
        }
        break;

      // text is the full transcript so far, so it replaces.
      case 'transcript.user.delta':
        partial('you', msg.text);
        logEvent('down', msg.type, msg.text);
        break;

      // delta is the next word only, so it appends.
      case 'transcript.agent.delta':
        logEvent('down', msg.type, msg.delta);
        if (msg.reply_id && msg.reply_id === printedReply) break;
        if (msg.reply_id !== liveReply) { liveReply = msg.reply_id; dropPartial('agent'); }
        partial('agent', appendDelta(partialText.agent || '', msg.delta));
        break;

      case 'transcript.user':
        addLine('you', msg.text);
        remember('caller', msg.text);
        logEvent('down', msg.type, msg.text);
        break;

      case 'transcript.agent':
        printedReply = msg.reply_id != null ? msg.reply_id : printedReply;
        addLine('agent', msg.text);
        remember('agent', msg.text);
        logEvent('down', msg.type, msg.text);
        break;

      case 'tool.call':
        onToolCall(msg);
        break;

      case 'session.ended':
        logEvent('down', msg.type);
        pushRecord('session_ended');
        ws.close();
        break;

      case 'session.error':
        setStatus('error', msg.message);
        logEvent('down', msg.type, `${msg.code}: ${msg.message}`);
        break;

      default:
        logEvent('down', msg.type);
    }
  }

  function readyFlag(value) { ready = value; }

  // --- the tool calls ---
  // Both tools are http tools: AssemblyAI posts them to Django itself, so
  // these are notifications, not requests for an answer. The page mirrors what
  // was extracted and then reads the result back off our own API.
  function onToolCall(msg) {
    const args = msg.arguments || {};
    logEvent('down', msg.type, `${msg.name} ${JSON.stringify(args)}`);

    // Never render the digits the caller just spoke to prove who they are.
    const shown = msg.name === 'verify_policyholder'
      ? { ...args, phone_last4: '••••' }
      : args;
    addLine('tool', `${msg.name}(${JSON.stringify(shown)})`);
    record.toolCalls.push({ name: msg.name, arguments: shown, at: relativeNow() });
    remember('tool', `${msg.name}(${JSON.stringify(shown)})`);

    if (!CFG.toolLive) {
      addLine('system', 'Tools are not published — set a public URL under Agent.');
      return;
    }

    if (msg.name === 'verify_policyholder') {
      awaitIdentity();
      return;
    }
    if (msg.name === 'log_claim') {
      fillClaim(args);
      showTab('claim');
      awaitClaim(args.policy_number);
      return;
    }

    if (msg.name === 'request_human') {
      addLine('system', `Adjuster requested · ${(args.reason || 'caller request').replace(/_/g, ' ')}`);
      return;
    }

    if (msg.name === 'end_call') {
      // Ivy is done. Let the closing line finish playing, then hang up — the
      // server cuts the session too, but a beat later and only as a backstop.
      hangingUp = true;
      addLine('system', `Ivy ended the call · ${args.reason || 'finished'}`);
    }
  }

  const relativeNow = () =>
    callStart ? Number(((Date.now() - callStart) / 1000).toFixed(1)) : 0;

  // reply.done fires when the audio has been sent, not when it has been heard.
  // The recorder knows how far ahead the agent channel runs, which is exactly
  // how much is still queued to play.
  function agentAudioRemainingMs() {
    if (!recorder) return 1500;
    const ahead = (recorder.rightLen - recorder.position) / WIRE_RATE;
    return Math.min(20000, Math.max(600, Math.round(ahead * 1000)));
  }

  // Verification happens server side, so read the outcome back rather than
  // guessing it from the arguments.
  function awaitIdentity() {
    let tries = 0;
    const look = async () => {
      tries += 1;
      try {
        const res = await fetch(`${CFG.sessionUrl}?session=${encodeURIComponent(sessionId || '')}`);
        if (res.ok) {
          const data = await res.json();
          showVerifyTries(data);
          if (data.verified && data.policyholder) {
            showIdentity(data.policyholder);
            addLine('system', `Verified: ${data.policyholder.full_name}`);
            return;
          }
        }
      } catch (error) {
        /* keep trying */
      }
      if (tries < 10) setTimeout(look, 900);
    };
    setTimeout(look, 800);
  }

  function showVerifyTries(data) {
    const el = $('verify-tries');
    if (!el) return;
    if (data.verified) {
      el.hidden = true;
      return;
    }
    if (data.locked) {
      el.hidden = false;
      el.textContent = 'Verification locked. Ivy will give the callback number.';
      el.classList.add('locked');
      return;
    }
    if (typeof data.attempts_remaining === 'number') {
      el.hidden = false;
      el.classList.remove('locked');
      el.textContent = data.attempts_remaining === 1
        ? '1 attempt remaining'
        : `${data.attempts_remaining} attempts remaining`;
    }
  }

  function showIdentity(holder) {
    const card = $('identity');
    if (!card) return;
    card.hidden = false;
    $('id-name').textContent = holder.full_name;
    $('id-policy').textContent = holder.policy_number;
    $('id-vehicle').textContent = holder.vehicle_line || '—';
    $('id-cover').textContent =
      `${holder.coverage_label} · $${holder.deductible} deductible` +
      (holder.roadside_assistance ? ' · roadside' : ' · no roadside');
    $('id-status').textContent = holder.status_label;
    $('id-status').className = 'pill ' + (holder.status === 'active' ? 'ok' : 'warn');
    paintIntake();
  }

  function send(message) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(message));
  }

  // AssemblyAI posts the webhook, so the claim shows up in our database a beat
  // later. Poll briefly for the row rather than guessing what was written.
  //
  // Only rows filed since this call started count: a returning caller already
  // has claims under that policy number, and one of those is not this one.
  function awaitClaim(policyNumber) {
    let tries = 0;
    const after = (callStart || Date.now()) - 5000;
    const look = async () => {
      tries += 1;
      try {
        const res = await fetch(CFG.feedUrl);
        const { claims: all } = await res.json();
        const claims = all.filter((c) => new Date(c.created_at).getTime() >= after);
        const match = policyNumber
          ? claims.find((c) => c.policy_number === String(policyNumber).toUpperCase())
          : claims[0];
        if (match) {
          const reference = 'CV-' + String(match.id).padStart(5, '0');
          lastCall.claimId = match.id;
          lastCall.reference = reference;
          lastCall.sharePath = match.share_path || '';
          lastCall.notified = Boolean(match.notified);
          fillClaim({ ...match, claim_reference: reference });
          addLine('system', `Filed ${reference} · risk ${match.risk_score} (${match.priority})`);
          return;
        }
      } catch (error) {
        /* keep trying; the call matters more than the mirror */
      }
      if (tries < 12) setTimeout(look, 1000);
    };
    setTimeout(look, 900);
  }

  const FIELD_TEXT = {
    is_drivable: (v) => (v ? 'Yes, still drivable' : 'No, tow required'),
    injuries_reported: (v) => (v ? 'Yes' : 'None reported'),
    incident_type: (v) => String(v).charAt(0).toUpperCase() + String(v).slice(1),
    severity: (v) => String(v).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
  };

  function fillClaim(data) {
    for (const row of document.querySelectorAll('#fnol .fnol-row')) {
      const field = row.dataset.field;
      if (!(field in data) || data[field] === null || data[field] === '') continue;
      const format = FIELD_TEXT[field];
      row.querySelector('.v').textContent = format ? format(data[field]) : String(data[field]);
      row.classList.add('filled');
    }
    paintIntake();
  }

  const INTAKE = [
    { step: 'verify', field: null },
    { step: 'incident', field: 'incident_type' },
    { step: 'location', field: 'location' },
    { step: 'drivable', field: 'is_drivable' },
    { step: 'filed', field: 'claim_reference' },
  ];

  function paintIntake() {
    const identityOn = $('identity') && !$('identity').hidden;
    const filled = (field) => {
      const row = document.querySelector(`#fnol .fnol-row[data-field="${field}"]`);
      return row && row.classList.contains('filled');
    };
    const core = ['policy_number', 'incident_type', 'location', 'is_drivable'];
    const have = core.filter(filled).length;
    const progress = $('claim-progress');
    if (progress) {
      if (filled('claim_reference')) progress.textContent = 'Claim filed';
      else if (have === 0 && !identityOn) progress.textContent = '0 of 4 fields · waiting to start';
      else progress.textContent = `${have} of ${core.length} required fields`;
    }
    let current = true;
    for (const item of INTAKE) {
      const el = document.querySelector(`#intake [data-step="${item.step}"]`);
      if (!el) continue;
      const done = item.step === 'verify' ? identityOn : filled(item.field);
      el.classList.toggle('done', done);
      el.classList.toggle('on', !done && current);
      if (!done) current = false;
    }
  }

  function stop() {
    // Close cleanly so the session record ends, falling back to the socket.
    if (ws && ws.readyState === 1) {
      send({ type: 'session.end' });
      logEvent('up', 'session.end');
      const socket = ws;
      setTimeout(() => { if (socket.readyState === 1) socket.close(); }, 3000);
    } else if (ws) {
      ws.close();
    }
    playback && playback.port.postMessage('stop');
    mic && mic.getTracks().forEach((track) => track.stop());
    captureCtx && captureCtx.close();
    playbackCtx && playbackCtx.close();
    captureCtx = playbackCtx = playback = mic = null;
    reset();
    setStatus('idle');
  }

  async function finishRecording() {
    if (!recorder || !sessionId) {
      if (sessionId || lastCall.claimId || conversationId) showRecap();
      return;
    }
    const seconds = recorder.seconds;
    if (seconds < 1) {
      showRecap();
      return;
    }
    const mine = recorder;
    recorder = null;
    addLine('system', `Uploading ${Math.round(seconds)}s of audio…`);
    // The transcript post creates the call row this attaches to, so it goes
    // first and this waits for it.
    await pushRecord('client_end');
    const result = await mine.upload(CFG.recordingUrl, sessionId);
    addLine(
      'system',
      result
        ? `Recording saved (${(result.bytes / 1048576).toFixed(1)} MB) — playable below and on the board.`
        : 'Recording could not be uploaded.'
    );
    if (result && result.url) lastCall.recordingUrl = result.url;
    else if (conversationId) lastCall.recordingUrl = `/api/conversations/${conversationId}/recording/`;
    showRecap();
  }

  function hideRecap() {
    const card = $('call-recap');
    if (card) card.hidden = true;
    const audio = $('recap-audio');
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
    }
  }

  function showRecap() {
    const card = $('call-recap');
    if (!card) return;
    const empty = $('transcript-empty');
    if (empty) empty.hidden = false;
    card.hidden = false;
    const filed = Boolean(lastCall.reference);
    $('recap-title').textContent = filed ? lastCall.reference + ' filed' : 'Call ended';
    $('recap-body').textContent = filed
      ? (lastCall.notified
        ? 'Ivy filed the claim and sent the share link. Open it on the board or send photos from the phone.'
        : 'Ivy filed the claim. Open it on the dispatcher board, or play the tape back here.')
      : 'Nothing was filed on that call. The transcript is still on the board.';
    const share = $('recap-share');
    if (share) {
      if (lastCall.sharePath) {
        share.hidden = false;
        share.href = lastCall.sharePath;
      } else {
        share.hidden = true;
      }
    }
    const board = $('recap-board');
    if (board) {
      if (conversationId) board.href = `${CFG.dashboardUrl}#call-${conversationId}`;
      else if (lastCall.claimId) board.href = `${CFG.dashboardUrl}#claim-${lastCall.claimId}`;
      else board.href = CFG.dashboardUrl;
    }
    const player = $('recap-player');
    const audio = $('recap-audio');
    if (player && audio && lastCall.recordingUrl) {
      player.hidden = false;
      audio.src = lastCall.recordingUrl;
    } else if (player) {
      player.hidden = true;
    }
  }

  function reset() {
    clearInterval(timer);
    clearInterval(recordTimer);
    finishRecording();
    clearPartials();
    open.forEach((run) => paint(run, true));
    open.clear();
    readyFlag(false);
    micLevel = agentLevel = 0;
    paintOrb();
    document.body.classList.remove('live', 'listening', 'speaking');
    $('call-btn').disabled = false;
    $('mic').disabled = false;
    $('call-btn').textContent = 'Start call';
    $('call-btn').classList.remove('end');
    const label = $('orb-label');
    if (label) label.textContent = 'Press start when you are ready';
  }

  function setStatus(state, detail) {
    $('status').className = 'status ' + state;
    const idleLabel = state === 'idle' ? 'Ready' : state;
    $('status-text').textContent = detail || idleLabel;
    const label = $('orb-label');
    if (label) {
      label.textContent = {
        idle: 'Press start when you are ready',
        connecting: 'Connecting to Ivy…',
        listening: 'Listening',
        speaking: 'Ivy is speaking',
      }[state] || (detail || state);
    }
    document.body.classList.toggle('listening', state === 'listening');
    document.body.classList.toggle('speaking', state === 'speaking');
  }

  // $4.50 an hour, the list price at assemblyai.com/pricing. Billing is per
  // session minute, so the running figure is an estimate, not an invoice.
  const COST_PER_SECOND = 4.5 / 3600;
  function tick() {
    const seconds = Math.floor((Date.now() - callStart) / 1000);
    $('elapsed').textContent = Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
    $('cost').textContent = '$' + (seconds * COST_PER_SECOND).toFixed(3);
  }

  // --- transcript ---
  const partialText = {}, partialEl = {};
  // The full reply arrives once its audio has been sent, which beats the audio
  // playing out, so deltas keep coming after the line is printed. printedReply
  // stops them rebuilding the same sentence underneath it.
  let liveReply = null, printedReply = null;

  // Deltas arrive with a leading space sometimes and without it other times, so
  // add one only when neither side has one and the delta is not punctuation.
  const ATTACHES_LEFT = /^[.,!?;:%°)\]}…'"’”]/;
  const NO_SPACE_AFTER = /[([{$\-\/'"‘“]$/;

  function appendDelta(text, delta) {
    if (!delta) return text;
    if (!text) return delta;
    if (/^\s/.test(delta) || /\s$/.test(text)) return text + delta;
    if (ATTACHES_LEFT.test(delta) || NO_SPACE_AFTER.test(text)) return text + delta;
    return text + ' ' + delta;
  }

  function dropPartial(who) {
    partialEl[who] && partialEl[who].remove();
    delete partialEl[who];
    delete partialText[who];
  }

  function transcriptLine(who, text, cls) {
    const line = document.createElement('div');
    line.className = 'line ' + who + (cls ? ' ' + cls : '');
    const label = document.createElement('span');
    label.className = 'who';
    label.textContent = who === 'agent' ? 'Ivy' : who === 'tool' ? 'tool' : who === 'system' ? '' : 'you';
    const body = document.createElement('span');
    body.className = 'said';
    body.textContent = text;
    line.append(label, body);
    return line;
  }

  function clearEmpty() {
    const empty = $('transcript-empty');
    if (empty) empty.remove();
  }

  function scroll(el) { el.scrollTop = el.scrollHeight; }

  function partial(who, text) {
    clearEmpty();
    partialText[who] = text;
    if (partialEl[who]) {
      partialEl[who].querySelector('.said').textContent = text;
    } else {
      partialEl[who] = transcriptLine(who, text, 'partial');
      $('transcript').append(partialEl[who]);
    }
    scroll($('transcript'));
  }

  function addLine(who, text) {
    clearEmpty();
    dropPartial(who);
    $('transcript').append(transcriptLine(who, text));
    scroll($('transcript'));
  }

  function clearPartials() {
    for (const who of Object.keys(partialEl)) dropPartial(who);
    liveReply = printedReply = null;
  }

  // --- event log ---
  // Audio frames arrive ~190 times a second each way, so these types hold a row
  // open and count into it. Both streams run at once, hence a row per key.
  const COALESCE = new Set(['input.audio', 'reply.audio', 'transcript.user.delta', 'transcript.agent.delta']);
  const open = new Map();

  function eventRow(direction, type, detail) {
    const row = document.createElement('div');
    row.className = 'event ' + direction;
    const at = document.createElement('span');
    at.className = 'at';
    at.textContent = (callStart ? (Date.now() - callStart) / 1000 : 0).toFixed(1) + 's';
    const arrow = document.createElement('span');
    arrow.className = 'dir';
    arrow.textContent = direction === 'up' ? '↑' : '↓';
    const name = document.createElement('span');
    name.className = 'type';
    name.textContent = type;
    const count = document.createElement('span');
    count.className = 'count';
    const info = document.createElement('span');
    info.className = 'detail';
    if (detail) info.textContent = detail;
    row.append(at, arrow, name, count, info);
    return row;
  }

  // Ten repaints a second, plus one when the run closes.
  function paint(live, final) {
    const now = performance.now();
    if (!final && now - live.painted < 100) return;
    live.painted = now;
    live.row.querySelector('.count').textContent = live.count > 1 ? '×' + live.count : '';
    if (live.detail) live.row.querySelector('.detail').textContent = live.detail;
  }

  function logEvent(direction, type, detail) {
    const log = $('events');
    const placeholder = $('events-empty');
    if (placeholder) placeholder.remove();
    const key = direction + ' ' + type;
    const live = open.get(key);
    if (live) {
      live.count += 1;
      if (detail) live.detail = detail;
      paint(live);
      return;
    }
    // A real event closes the open runs, so the next burst starts a new row.
    if (!COALESCE.has(type)) {
      open.forEach((run) => paint(run, true));
      open.clear();
    }
    // Only follow the tail if the reader is there.
    const pane = $('pane-events');
    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
    const row = eventRow(direction, type, detail);
    log.append(row);
    while (log.children.length > 400) log.firstChild.remove();
    if (COALESCE.has(type)) open.set(key, { row, count: 1, detail, painted: 0 });
    if (atBottom) scroll(pane);
  }

  document.querySelectorAll('[data-copy]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { /* ignore */ }
      const hint = btn.querySelector('.copy-hint') || btn;
      const prior = hint.textContent;
      hint.textContent = 'Copied';
      setTimeout(() => { hint.textContent = prior === 'Copied' ? 'Copy' : prior; }, 1400);
    });
  });

  paintIntake();

  const dismiss = $('recap-dismiss');
  if (dismiss) dismiss.onclick = hideRecap;

  function applyPolicyHash() {
    const code = (location.hash || '').replace(/^#/, '').toUpperCase();
    if (!/^PV[A-Z0-9]+$/.test(code) || !CFG.policyholdersUrl) return;
    fetch(`${CFG.policyholdersUrl}?policy=${encodeURIComponent(code)}`)
      .then((res) => res.json())
      .then((data) => {
        const holder = (data.policyholders || []).find((h) => h.policy_number === code);
        if (!holder) return;
        const policyBtn = document.querySelector('[data-copy]#demo-policy')
          || $('demo-policy')?.closest('[data-copy]');
        const lastBtn = $('demo-last4')?.closest('[data-copy]');
        if ($('demo-policy')) $('demo-policy').textContent = holder.policy_number;
        if ($('demo-last4')) $('demo-last4').textContent = holder.phone_last4 || '';
        if (policyBtn) policyBtn.dataset.copy = holder.policy_number;
        if (lastBtn) lastBtn.dataset.copy = holder.phone_last4 || '';
        const who = $('demo-who');
        if (who) {
          who.innerHTML =
            `You are <b>${escapeHtml(holder.full_name)}</b>${holder.vehicle_line ? `, ${escapeHtml(holder.vehicle_line)}` : ''}.
            More testers on the <a href="/directory/">directory</a>.`;
        }
        const kicker = document.querySelector('.start-kicker');
        if (kicker) kicker.textContent = 'Calling as ' + holder.first_name;
      })
      .catch(() => { /* directory is optional for the call itself */ });
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );
  }

  applyPolicyHash();
  window.addEventListener('hashchange', applyPolicyHash);
})();
