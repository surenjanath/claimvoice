/* Keeps a page current without anyone pressing reload.
 *
 * One heartbeat for the whole page, not a timer per widget. Each subscriber
 * names the counters it cares about; when one of them moves, its callback runs
 * and fetches the real data. A quiet minute costs a few hundred bytes, not a
 * feed per page per tick.
 *
 * Three things that matter more than the polling itself:
 *
 *   - It stops when the tab is hidden. A dashboard left open overnight would
 *     otherwise make tens of thousands of requests nobody is looking at, and on
 *     a free dyno that is the whole budget.
 *   - It backs off when the server is unreachable, so a restart is not met with
 *     a tab hammering a door that is not open yet.
 *   - It catches up the moment the tab comes back, so switching to it never
 *     shows a stale board.
 */
window.Live = (() => {
  const BASE_MS = 3000;
  const MAX_BACKOFF_MS = 60000;

  const subscribers = [];
  let timer = null;
  let failures = 0;
  let running = false;
  const listeners = new Set();

  const fingerprint = (pulse, keys) =>
    keys.map((key) => JSON.stringify(pulse[key] ?? null)).join('|');

  async function tick() {
    if (document.hidden) return schedule();
    try {
      const res = await fetch(window.CLAIMVOICE.pulseUrl, {
        headers: { 'X-Requested-With': 'live' },
      });
      if (res.status === 401) {
        // The desk session expired. Every feed behind this page is shut too,
        // so reload and let the server send the reader to the login form,
        // rather than leave them on a board that will never move again.
        clearTimeout(timer);
        window.location.reload();
        return;
      }
      if (!res.ok) throw new Error(res.status);
      const pulse = await res.json();
      failures = 0;
      announce({ ok: true, at: new Date() });

      for (const sub of subscribers) {
        const mark = fingerprint(pulse, sub.keys);
        if (sub.mark === mark) continue;
        const first = sub.mark === undefined;
        const seen = sub.seen || null;
        sub.mark = mark;
        sub.seen = pulse;
        // The first tick establishes a baseline; a page has already loaded its
        // own data by then and does not need it fetched twice.
        if (!first || sub.immediate) invoke(sub, pulse, seen);
      }
    } catch (error) {
      failures += 1;
      announce({ ok: false, at: new Date() });
    }
    schedule();
  }

  // One broken subscriber must not stop the heartbeat, and its miss must not
  // be recorded as handled: RETRY leaves the mark unequal to any fingerprint,
  // so the next tick runs it again rather than waiting for another change.
  const RETRY = '';

  function invoke(sub, pulse, seen) {
    let result;
    try {
      result = sub.run(pulse, seen);
    } catch (error) {
      sub.mark = RETRY;
      return;
    }
    // Subscribers fetch, so most of them hand back a promise; a rejected one
    // is the same miss as a thrown error.
    if (result && typeof result.catch === 'function') {
      result.catch(() => { sub.mark = RETRY; });
    }
  }

  function delay() {
    if (!failures) return BASE_MS;
    return Math.min(MAX_BACKOFF_MS, BASE_MS * 2 ** Math.min(failures, 5));
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(tick, delay());
  }

  function announce(state) {
    for (const listener of listeners) {
      try {
        listener({ ...state, failures, paused: document.hidden });
      } catch (error) {
        /* a status pill is not worth breaking the loop over */
      }
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearTimeout(timer);
      announce({ ok: true, at: null });
      return;
    }
    // Back on screen: catch up now rather than at the next tick.
    failures = 0;
    clearTimeout(timer);
    tick();
  });

  return {
    /** watch(['claims','dispatches'], fn) — fn(pulse, previous) runs when any
     *  of them changes; previous is the pulse it last ran on, null the first
     *  time, so a page can tell what moved and fetch only that much. */
    watch(keys, run, options = {}) {
      subscribers.push({ keys, run, immediate: Boolean(options.immediate) });
      if (!running) {
        running = true;
        tick();
      }
      return () => {
        const at = subscribers.findIndex((s) => s.run === run);
        if (at > -1) subscribers.splice(at, 1);
      };
    },
    /** Told whenever the heartbeat succeeds or fails, for a status indicator. */
    onStatus(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** Force a beat — after an action whose result the page wants immediately. */
    poke() {
      clearTimeout(timer);
      failures = 0;
      tick();
    },
  };
})();
