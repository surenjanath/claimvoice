# ClaimVoice — submission write-up

**AssemblyAI Voice Agent Hackathon (lablab.ai), September 2026**

## One-line pitch

Ivy answers the call when a driver crashes, takes their First Notice of Loss
while they're still on the line, and gets a tow moving — all before hold
music would have finished playing on a real claims line.

## The problem

A driver in a collision calls their insurer and gets: hold time, a scripted
intake form read at them by someone who can't see the road they're stranded
on, and — if a tow is needed — a second call to a dispatch desk that starts
the whole story over. High stress, slow, and every step is manual data entry
that already happened once, out loud, thirty seconds earlier.

## The solution

ClaimVoice is one Django app plus one AssemblyAI voice agent (Ivy) that:

1. **Verifies who's calling** before saying anything about a policy — policy
   number + last four digits of the phone on file, with a fail response that
   is identical whether the policy exists or not (confirming a policy number
   is real is itself a disclosure).
2. **Takes the claim conversationally** — safety, incident type, location,
   vehicle, drivability — and fires `log_claim`, an AssemblyAI HTTP tool, the
   moment it has everything.
3. **Scores risk and dispatches a tow** synchronously, in the same webhook
   call that files the claim, and reads the reference number and ETA back to
   the caller before they hang up.
4. **Chases the tow if it runs late**, ranking recovery operators by distance
   and escalating past anyone already tried.
5. **Hands off to a person** — Rae, a second agent, calls the recovery
   operator; a live Twilio transfer moves the *caller* to a human dispatcher
   when Ivy isn't the right tool for the moment (injuries, a rollover, a
   child in the back seat).

Everything lands on a dispatcher board in real time, including the full call
recording and transcript, and an insights page that shows not just what
happened but where the agent itself keeps needing a second attempt.

## How AssemblyAI is used

- **Universal-3** end-to-end for STT · LLM · TTS over the Agents API — one
  websocket, no separate ASR/LLM/TTS integration to wire up.
- **HTTP tool calling** — `verify_policyholder`, `log_claim`,
  `request_human`, `end_call` — each backed by a Django webhook, published
  from a single `agent.json` / `AgentProfile` row.
- **A second published agent** (Rae, `vendor_agent.json`) for the outbound
  leg to recovery operators — same platform, a different persona and a
  one-tool script.
- **SIP trunking to a real phone number** via Twilio, so the exact same
  agent answers a browser tab or a PSTN call with no media server or audio
  bridge of our own in the path.

## What was hard, and what we shipped instead of hand-waving it

These are the parts worth a judge's attention — each is a real failure mode
we hit, diagnosed, and fixed with a test that pins the fix down:

- **A tool schema is picky about *how* the model fills it.** One property
  worded as "one sentence describing what happened, in your own words"
  silently stops the tool from firing at all — no error, just Ivy saying
  "let me get that filed" forever. Found by bisecting the schema field by
  field against the live API; `description` was cut, `severity` became an
  enum instead, and a test fails the build if a composed field creeps back.
- **The model infers drivability from damage description if you let it.**
  "The airbags went off" became `is_drivable: false` on three runs out of
  three, with the prompt saying only "never infer it." The fix isn't a
  better prompt — it's a third enum value, `not_asked`, so the webhook can
  refuse the guess and hand back the actual question to ask.
- **A stereo recording only helps if the two sides don't drift.** The
  caller's mic *is* the clock; the agent's audio arrives in bursts faster
  than playback, so each reply is anchored to the caller's timeline at the
  moment it starts, and a barge-in truncates the channel to where playback
  actually stopped — because the caller never heard the rest.
- **The secret the identity check rests on was sitting in plain text.** The
  spoken last-four digits lived in the transcript and were audible in the
  recording. Both are redacted now — text surgically, inside just the
  answering turn; audio by blanking that same window on the caller's channel
  only, so Ivy's side of the question is still audible for review.
- **A free hosting plan has no worker dyno.** `watch_dispatches` and
  `watch_handoffs` — the loops that chase a late tow and close out a stuck
  transfer — are one-pass-per-run by design, meant for cron. We added
  `POST /api/tick/`, a secret-gated endpoint an external pinger hits every
  few minutes, so those loops actually run without a paid Render add-on.
- **No silent dead ends.** Empty rota, everyone already tried, Twilio's own
  status callback lost in transit — every overflow path ends in the caller
  being told something true and a fallback number being texted, never dead
  air. `manage.py watch_handoffs` reaps anything stuck past its window.

## Tech stack

Django 6 · SQLite (Postgres in production) · AssemblyAI Agents API
(Universal-3, HTTP tools) · Twilio (SIP trunk + SMS) · Whitenoise ·
Sentry · Render (free-tier blueprint deploy).

## Where things stand

- **201 automated tests**, including two race conditions (a webhook posted
  before its session was known; audio arriving before its redaction spans
  were computed) that were real bugs before they were tests.
- A **risk scorer** that's a readable rule set, not a model — every point
  scored has a stated reason, and the reasons are what the dashboard shows.
- An **insights page**: funnel by stage, which question needs a second
  attempt most often, risk bands, identity pass rate — the point being not
  "what happened" but "what to fix in the prompt next."
- Deployable end to end on Render's free tier, including the background
  loops, via the blueprint in `render.yaml`.

## Links

- Repo: https://github.com/surenjanath/claimvoice
- Demo: press **Start call** on `/`, or run `node tools/simulate_call.mjs`
  for a no-microphone walkthrough that talks to a real running server.
