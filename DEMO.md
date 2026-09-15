# Demo runbook

Two versions: the **full walkthrough** (5-7 min, for a live judging call) and
the **video cut** (60-90s, for the submission video). Both pull from the same
beats — the video is just the highlight reel.

## Before you start

```sh
./run.sh                              # tunnel + publish + server, one command
# in a second terminal, once it's up:
node tools/simulate_call.mjs          # proves the loop end to end, no mic needed
```

Have `/dashboard/` open in one tab, `/insights/` in another. `manage.py
seed_claims --count 12` gives the board something on it before you start if
you want it non-empty from frame one.

## Full walkthrough

1. **The call.** Press Start on `/`, or run `simulate_call.mjs` with the
   dashboard visible on a second monitor. Say who you are only when asked —
   Ivy asks for the policy number and last four *before* anything else. Call
   in as Dana Whitfield: `PV482193` / `2887`.
2. **The identity check fails on purpose.** Run
   `WRONG_FIRST=1 node tools/simulate_call.mjs` — Ivy asks again without
   confirming or denying the policy exists. This is the line worth reading
   out loud: a wrong answer gets the same response whether the policy is
   real or not, because confirming it's real is itself a disclosure.
3. **The claim lands on the board live.** Switch to `/dashboard/` mid-call
   or right after — the row appears with risk score, factors, and a tow
   already dispatched, before the caller has hung up.
4. **Click into the call.** Conversations tab: transcript turn by turn,
   click a line, the stereo recording jumps to that moment. Point out the
   caller/Ivy channel split and that the spoken verification digits are
   blanked in both the transcript and the audio.
5. **A tow that runs late.** `python manage.py watch_dispatches --dry-run`
   against a claim whose dispatch you've backdated (or just narrate this one
   — it's the harder feature to fake live): first pass chases the operator
   already assigned, second pass reassigns to the next-closest one and tells
   the caller why.
6. **Ask for a person.** Trigger `request_human` (say "I want to talk to a
   person" mid-call) — the **Waiting** tab lights up, a dispatcher can press
   *Take this call*. Explain the honesty constraint: Ivy never says "transfer
   coming" unless `LIVE_TRANSFERS` is actually on.
7. **`/insights/`.** Funnel (answered → verified → filed → dispatched),
   which question needs a second attempt most, risk bands. This is the
   "how do we make Ivy better" page, not just a log.
8. **The one API gotcha, told as a story.** `description` as a free-text
   field silently kills the tool call — no error, the model just never files
   anything. Bisected field by field against the live API; there's a test
   that fails the build if a composed field creeps back in.

## Video cut (60-90s)

Pick five beats, in this order — they're the ones that read as *product*,
not *demo*:

1. The problem, one line: hold times, repeated intake, a second call for the
   tow.
2. The call happening — audio waveform / live transcript filling in, ending
   on Ivy reading back the claim reference and tow ETA.
3. The board updating in real time as she says the reference number.
4. One hard-problem callout, on screen as text over the architecture
   diagram: the identity check's symmetric failure response, *or* the
   drivability guardrail (`is_drivable: not_asked` forcing an honest "I
   haven't asked yet" instead of an inferred guess).
5. Close on the numbers: 201 tests, free-tier deployable, one line on what's
   next (a real phone number via Twilio SIP, already wired).

## Scenarios available without touching a microphone

`node tools/simulate_call.mjs` with overrides:

| what it shows | command |
| --- | --- |
| default collision, tow dispatched | `node tools/simulate_call.mjs` |
| a different caller/policy | `POLICY=PV305518 LAST4=3076 node tools/simulate_call.mjs` |
| identity check failing once | `WRONG_FIRST=1 node tools/simulate_call.mjs` |
| a different voice | `SAY_VOICE=Daniel node tools/simulate_call.mjs` |

The dashboard's **Inject demo claim** button cycles four canned scenarios
without a call at all — collision, a second collision (vehicle mismatch
check), storm damage with an injury, and theft (which never dispatches a
tow) — useful for populating the board fast before a live walkthrough.
