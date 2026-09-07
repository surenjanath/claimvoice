/* Builds the call recording.
 *
 * Two streams arrive separately: the microphone as it is captured, and Ivy's
 * reply audio as it is played. Both are 24 kHz PCM16, so they are written into
 * one stereo timeline — caller on the left, Ivy on the right — indexed by
 * sample position rather than arrival time. That keeps them aligned even
 * though the API sends a whole reply's audio faster than it plays.
 *
 * Stereo rather than a mix, because the interesting moments are the overlaps:
 * a barge-in is obvious when the two sides are on separate channels.
 */
window.CallRecorder = class CallRecorder {
  constructor(sampleRate = 24000, capSeconds = 900) {
    this.rate = sampleRate;
    this.cap = sampleRate * capSeconds;
    // Grown as needed rather than allocated for the cap: most calls are short.
    this.left = new Int16Array(sampleRate * 60);
    this.right = new Int16Array(sampleRate * 60);
    this.leftLen = 0;
    this.rightLen = 0;
    this.stopped = false;
  }

  _fit(channel, needed) {
    const buffer = channel === 'left' ? this.left : this.right;
    if (needed <= buffer.length) return buffer;
    let size = buffer.length;
    while (size < needed) size *= 2;
    size = Math.min(size, this.cap);
    const grown = new Int16Array(size);
    grown.set(buffer.subarray(0, Math.min(buffer.length, size)));
    if (channel === 'left') this.left = grown;
    else this.right = grown;
    return grown;
  }

  /** Caller audio, in capture order — this channel is the clock. */
  addCaller(samples) {
    if (this.stopped) return;
    const end = this.leftLen + samples.length;
    if (end > this.cap) return;
    this._fit('left', end).set(samples, this.leftLen);
    this.leftLen = end;
  }

  /** Where the caller clock is now, in samples. The mic runs continuously, so
   * its length is real time and everything else aligns against it. */
  get position() {
    return this.leftLen;
  }

  /** A reply is starting: jump the agent channel to now, so the whole reply is
   * not stacked against the end of the previous one. Reply audio arrives
   * faster than it plays, so arrival order alone would drift forward. */
  alignAgent(position) {
    if (position > this.rightLen) {
      this._fit('right', position);
      this.rightLen = Math.min(position, this.cap);
    }
  }

  /** Barge-in: the queued audio was dropped from the speaker, so the caller
   * never heard it. Cut the channel back to where playback actually stopped. */
  truncateAgent(position) {
    if (position < this.rightLen) {
      this.right.fill(0, Math.max(0, position), this.rightLen);
      this.rightLen = Math.max(0, position);
    }
  }

  /** Ivy's audio, exactly as the API sent it. */
  addAgent(samples) {
    if (this.stopped) return;
    const end = this.rightLen + samples.length;
    if (end > this.cap) return;
    this._fit('right', end).set(samples, this.rightLen);
    this.rightLen = end;
  }

  get seconds() {
    return Math.max(this.leftLen, this.rightLen) / this.rate;
  }

  /** A 16-bit stereo WAV of the whole call. */
  toWavBlob() {
    this.stopped = true;
    const frames = Math.max(this.leftLen, this.rightLen);
    if (!frames) return null;

    const bytes = 44 + frames * 4; // stereo, 2 bytes a sample
    const buffer = new ArrayBuffer(bytes);
    const view = new DataView(buffer);
    const ascii = (offset, text) => {
      for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
    };

    ascii(0, 'RIFF');
    view.setUint32(4, bytes - 8, true);
    ascii(8, 'WAVE');
    ascii(12, 'fmt ');
    view.setUint32(16, 16, true); // PCM header length
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, 2, true); // channels
    view.setUint32(24, this.rate, true);
    view.setUint32(28, this.rate * 4, true); // byte rate
    view.setUint16(32, 4, true); // block align
    view.setUint16(34, 16, true); // bits per sample
    ascii(36, 'data');
    view.setUint32(40, frames * 4, true);

    let offset = 44;
    for (let i = 0; i < frames; i++) {
      view.setInt16(offset, i < this.leftLen ? this.left[i] : 0, true);
      view.setInt16(offset + 2, i < this.rightLen ? this.right[i] : 0, true);
      offset += 4;
    }
    return new Blob([buffer], { type: 'audio/wav' });
  }

  /** Hand the finished call to the server. Failure is not worth a alert. */
  async upload(url, sessionId) {
    const blob = this.toWavBlob();
    if (!blob || !sessionId) return null;
    const form = new FormData();
    form.append('session_id', sessionId);
    form.append('audio', blob, 'call.wav');
    try {
      const res = await fetch(url, { method: 'POST', body: form });
      return res.ok ? await res.json() : null;
    } catch (error) {
      return null;
    }
  }
};
