#!/usr/bin/env node
/* Prove the page heartbeat before trusting a board to it.
 *
 *   node tools/check_live.mjs
 *
 * claims/static/claims/js/live.js is the one piece of this that the Django
 * suite cannot reach, and the things it has to get right are the things a
 * demo notices only when they fail: a page that never refetches, a page that
 * refetches forever, a widget whose fetch failed once and stayed dead. So it
 * runs here against a fake pulse and a fake clock, with no browser.
 *
 * Exits non-zero on the first assertion that does not hold.
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const source = fs.readFileSync(
  path.join(root, 'claims/static/claims/js/live.js'),
  'utf8'
);

// A page, minus the page: enough window and document for the module to load,
// and a fetch that hands back the pulses this case wants in order. The last
// one repeats, so a case can end on a steady state.
function load(pulses) {
  const handlers = {};
  let served = 0;
  let reloaded = 0;
  const sandbox = {
    window: {
      CLAIMVOICE: { pulseUrl: '/api/pulse/' },
      location: { reload: () => { reloaded += 1; } },
    },
    document: {
      hidden: false,
      addEventListener: (name, fn) => { handlers[name] = fn; },
    },
    setTimeout, clearTimeout, JSON, Date, Math, console,
    fetch: async () => {
      const pulse = pulses[Math.min(served++, pulses.length - 1)];
      if (pulse === 'offline') throw new Error('offline');
      if (pulse === 'locked') return { ok: false, status: 401 };
      return { ok: true, json: async () => pulse };
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return {
    Live: sandbox.window.Live,
    document: sandbox.document,
    handlers,
    requests: () => served,
    reloads: () => reloaded,
  };
}

const beat = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));

const cases = {
  async 'a subscriber runs once on the first beat, then only when it changes'() {
    const quiet = { claims: { n: 1, last: 5 }, notes: { n: 0, last: 0 } };
    const { Live } = load([quiet]);
    const runs = [];
    Live.watch(['claims'], (pulse, seen) => runs.push(seen), { immediate: true });
    await beat();
    assert.equal(runs.length, 1);
    assert.equal(runs[0], null, 'nothing came before the first run');
    Live.poke();
    await beat();
    assert.equal(runs.length, 1, 'an unchanged counter is not a reason to refetch');
  },

  async 'a changed counter hands the subscriber the pulse it last acted on'() {
    const before = { claims: { n: 1, last: 5 } };
    const after = { claims: { n: 2, last: 6 } };
    const { Live } = load([before, after]);
    const seen = [];
    Live.watch(['claims'], (pulse, previous) => {
      seen.push([pulse.claims.last, previous && previous.claims.last]);
    }, { immediate: true });
    await beat();
    Live.poke();
    await beat();
    assert.deepEqual(seen, [[5, null], [6, 5]]);
  },

  async 'a subscriber whose fetch failed is tried again, not written off'() {
    const pulse = { claims: { n: 1, last: 5 } };
    const { Live } = load([pulse]);
    let runs = 0;
    Live.watch(['claims'], async () => {
      runs += 1;
      if (runs === 1) throw new Error('the feed was down');
    }, { immediate: true });
    await beat();
    Live.poke();
    await beat();
    assert.equal(runs, 2, 'retried even though no counter moved');
    Live.poke();
    await beat();
    assert.equal(runs, 2, 'and stopped retrying once it worked');
  },

  async 'one broken widget does not take the heartbeat down'() {
    const pulse = { claims: { n: 1, last: 5 }, notes: { n: 1, last: 1 } };
    const { Live } = load([pulse]);
    let healthy = 0;
    Live.watch(['claims'], () => { throw new Error('broken widget'); }, { immediate: true });
    Live.watch(['notes'], () => { healthy += 1; }, { immediate: true });
    await beat();
    assert.equal(healthy, 1);
  },

  async 'an unreachable server is backed off, and the page is told'() {
    const { Live, requests } = load(['offline']);
    const states = [];
    Live.onStatus((state) => states.push(state));
    Live.watch(['claims'], () => {}, { immediate: true });
    await beat(400);
    assert.ok(states.some((state) => !state.ok), 'the failure was announced');
    assert.ok(requests() <= 2, `backed off; tried ${requests()} times in 400ms`);
  },

  async 'an expired desk session sends the reader to the login form'() {
    const { Live, requests, reloads } = load(['locked']);
    Live.watch(['claims'], () => {}, { immediate: true });
    await beat();
    assert.equal(reloads(), 1, 'reloaded so the server can redirect');
    const asked = requests();
    await beat(200);
    assert.equal(requests(), asked, 'and stopped asking rather than backing off');
  },

  async 'a hidden tab stops asking, and catches up when it comes back'() {
    const pulse = { claims: { n: 1, last: 5 } };
    const { Live, document, handlers, requests } = load([pulse]);
    Live.watch(['claims'], () => {}, { immediate: true });
    await beat();

    document.hidden = true;
    handlers.visibilitychange();
    const asleep = requests();
    Live.poke();
    await beat();
    assert.equal(requests(), asleep, 'a hidden tab fetched nothing, even asked directly');

    document.hidden = false;
    handlers.visibilitychange();
    await beat();
    assert.ok(requests() > asleep, 'and caught up the moment it came back');
  },
};

let failed = 0;
for (const [name, run] of Object.entries(cases)) {
  try {
    await run();
    console.log(`  ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`  FAIL ${name}\n       ${error.message}`);
  }
}
console.log(failed ? `\n${failed} failed` : '\nlive.js behaves');
process.exit(failed ? 1 : 0);
