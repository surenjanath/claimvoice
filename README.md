# ClaimVoice

**The AI first-responder for insurance claims intake.**

A driver crashes, calls, and talks to Ivy. She checks they are safe, takes the
policy number, the location, what happened and whether the car still drives —
then files the First Notice of Loss while they are still on the line. The claim
is scored, a tow is dispatched, and the row lands on the dispatcher board as she
reads the reference number back.

One Django app serves all three pieces:

| | |
| --- | --- |
| `/` | The voice client. Microphone in, Ivy out, live transcript, and the caller's identity and claim filling in as she works. |
| `/dashboard/` | The dispatcher board. Claims and risk on one tab, every call — transcript and audio — on the other. |
| `/insights/` | How the agent is doing, and which question it keeps getting wrong. |
| `/directory/` | The policy book Ivy verifies against — pick a person and call in as them. |
| `/settings/` | Ivy's voice, prompt and turn taking, published to AssemblyAI without a redeploy. |
| `/api/verify/` · `/api/log-claim/` | The two tool webhooks AssemblyAI posts to. |

## Run it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env               # add your ASSEMBLYAI_API_KEY
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_policies   # the book Ivy verifies against
.venv/bin/python manage.py seed_claims     # a board with something on it
brew install cloudflared           # so AssemblyAI can reach the webhooks
./run.sh
```

`run.sh` opens a public tunnel, publishes the agent pointed at it, starts the
server, and prints the link to share. Open it and press **Start call**.

Ivy will ask who you are. Call in as anyone from `/directory/` — say
**PV482193** and last four **2887** and you are Dana Whitfield, driving a silver
2019 Toyota Camry.

Without a tunnel, `manage.py runserver` still serves every page and the webhook,
but Ivy will talk and never file anything — see the constraint below.

### Talk to it without a microphone

```sh
node tools/simulate_call.mjs

# call in as someone else, or fluff the identity check once on purpose
POLICY=PV305518 LAST4=3076 node tools/simulate_call.mjs
WRONG_FIRST=1 node tools/simulate_call.mjs
```

Synthesises a caller with macOS `say`, holds the whole conversation, and exits
non-zero if no claim reaches the database. A full call takes about 60 seconds.

```
 41.7s  tool  verify_policyholder({"phone_last4":"••••","policy_number":"PV771004"})
 47.5s  ivy   Thank you Marcus, I have your policy here. Can you tell me what happened?
 64.6s  tool  log_claim({...,"is_drivable":"not_asked","vehicle":"2021 blue Honda Civic"})
 71.9s  ivy   I just need to check one more thing before I can file this.
               Is the Honda Civic still driveable?
 78.5s  tool  log_claim({...,"is_drivable":"no","severity":"fluid_leak"})
 79.8s  db    CV-00049 risk 71 (high), tow dispatched
```

## Identity, before anything else

Ivy says nothing about a policy until she knows who is calling. She asks for the
policy number and the last four digits of the phone number on file, and calls
`verify_policyholder`. Only then does she greet you by name and start taking the
claim — and she uses what came back, asking "is the Camry still driveable?"
rather than "what do you drive?".

The failure path is the part worth reading. A wrong answer returns the same body
whether the policy exists or not, because confirming that a policy number is real
is itself a disclosure:

```json
{
  "verified": false,
  "attempts_remaining": 2,
  "message": "That does not match what I have on file.",
  "instructions": "The check failed. Do not say whether the policy number exists, do not reveal any name or detail. Ask the caller to repeat..."
}
```

Two audiences, one object: `message` is the only part Ivy says out loud, and
`instructions` steers her without the caller ever hearing it. Four wrong answers
inside thirty minutes locks the policy out of the phone channel entirely and she
hands over the callback number.

Verification also carries the things a dispatcher would want her to know — a
lapsed policy, or no roadside cover — as instructions rather than as facts she
might read aloud. A tow on a policy without roadside cover is quoted as
chargeable instead of dispatched free.

## Every call, kept — including the audio

The Conversations tab holds each call in full: the recording, the transcript turn
by turn with timings, the tool calls inline, and which identity checks passed.
Click any line of the transcript and the audio jumps to that moment; the line
playing highlights itself as it goes.

The recording is a single stereo WAV with **the caller on the left channel and
Ivy on the right**, which is what makes it useful rather than merely present: a
barge-in, a talk-over, a pause that ran too long — all obvious when the two sides
are separated, all muddy in a mix.

Building it is not just "save the audio", because the two directions do not
arrive on the same clock. The microphone is real time, so its sample count *is*
the clock. Ivy's audio arrives faster than it plays — the API sends a whole reply
in a burst — so appending it in arrival order would drift further ahead with every
turn. Each reply is instead anchored to the caller's clock when it starts, and on
a barge-in the channel is truncated to where playback actually stopped, because
the caller never heard the rest.

The agent side is recorded from the API's own 24 kHz frames rather than from the
speaker, since a browser may quietly refuse to open an `AudioContext` at the rate
it was asked for and hand back 48 kHz instead.

AssemblyAI keeps session metadata but not the words, so transcripts come from
whatever held the call: the browser page and the simulator both post turns as
they happen. A phone call has no browser, so its record is built from its tool
calls, and `manage.py sync_calls` tops it up with duration and close reason from
the sessions API.

### The digits are never kept

Ivy asks for the last four digits of the phone number on the policy, and the
caller says them out loud. That answer is the secret the whole identity check
rests on, so a stored transcript or recording is the key sitting next to the
lock — enough to call back tomorrow as them.

The tool arguments were masked from the start. The spoken answer was not: it sat
in `turns` as text and stayed audible in every WAV. Both are removed now, on the
way in.

- **Transcript.** Only a caller turn that answers a question about the digits is
  touched, and only the digits inside it. The policy number survives — it is an
  identifier the agent reads back anyway, not a secret — and so does "interstate
  95 near exit 12", so the transcript still reads as a conversation and still
  shows whether Ivy asked the question at all.
- **Audio.** Masking the text records the window it was said in, and the
  recording is blanked across that window **on the caller's channel only**. Ivy's
  side is left intact, so you can still hear her ask — which is the part worth
  reviewing. Stereo earns its keep twice.

```
redacted window 37.0s - 43.8s
  caller channel   0        <- the spoken digits
  Ivy channel      19040    <- her question, kept
  caller elsewhere 26058    <- rest of the call untouched
```

`manage.py redact_calls [--dry-run]` sweeps anything recorded before this, and
covers the case where audio arrived before its transcript did, so the windows
were not yet known. It rewrites in place and keeps no copy, because a copy of
the thing you just removed is not a redaction.

### Which call does a webhook belong to?

AssemblyAI posts the tool webhooks itself and passes no session id, so the
webhook cannot name its own call. It finds it by looking for the call that is
**currently open** — clients register a session the moment it is ready and mark it
ended when they hang up, so an unended row started minutes ago is the call being
spoken on right now.

The obvious alternative — match the most recent row that has no session id —
looks right and is not. A leftover row from an earlier call stays "recent" long
after that call ended, so claims filed later were silently glued onto it. That is
exactly what happened here: a claim landed on a row from eighteen minutes earlier,
and the call that actually filed it showed no claim at all. Two tests pin the
behaviour down.

## Getting a truck moving

Taking the claim in ninety seconds is the easy half. The part that actually
helps a driver on a hard shoulder is the call afterwards — ring a recovery
operator, give them the location and the vehicle, ask how long, then tell the
driver. Rae makes that call.

```
dispatcher asks for a tow
  rang I-95 Rapid Tow (5.1 km away)
that operator is busy
  escalated to Harbor Point Recovery (129.5 km)
  caller told: The first operator could not take it, so we are arranging
               Harbor Point Recovery instead.
the next one takes it, 25 minutes
  dispatch now: Harbor Point Recovery · ETA 25 min · En route
  caller told: Harbor Point Recovery is on the way to Interstate 95
               northbound, just past Exit 12, about 25 minutes.
```

Rae is a second published agent (`vendor_agent.json`, `publish_agent --vendor`)
with a different job and a much shorter script: say who is calling, give the
location, get a yes or no and a number of minutes, and hang up. Dispatch desks
are busy. Her one tool is `record_eta`, and — like every tool here — every field
is copied or picked from a list, never composed.

Vendors are ranked by depot distance when the claim has a pin, by location words
when it does not, and an escalation excludes anyone already tried, so it moves
down the list instead of redialling the truck that just said no.

### When it runs late

`manage.py watch_dispatches [--loop 60]` compares each en-route tow against the
time it promised. First time over, it chases the operator that took the job.
Still over on the next pass, it reassigns to the next-closest one, cancels the
old dispatch and tells the caller. Out of operators, and the claim is handed to
a person.

Chasing the operator you already have is not another attempt at finding one, so
it does not count against the escalation budget — counting it stranded callers
who still had options.

### Two things that do not happen automatically

**Nothing dials a real number by default.** Every vendor in the book is in a
range reserved for fiction, a test asserts it, and even then a call is only
placed when the deployment has a registered number and has set `OUTBOUND_CALLS`.
Otherwise the call is recorded as simulated: same row, same escalation, same
updates to the caller, no phone ringing. That is also the only way to run this
before the Twilio step, since an outbound call needs a `from_number` AssemblyAI
knows about.

**Nothing calls the police.** A machine that can summon emergency services on
its own reading of a situation can send them to the wrong place, and a false
dispatch is somebody else's emergency going unanswered. When a claim looks like
one where you would want them — injuries reported, a fire or a rollover, a
vehicle stopped on a highway — the dispatcher gets the numbers and the reasons,
and makes that call themselves. The panel says so in as many words.

## Insights

`/insights/` is the other half of the dashboard: not what is happening, but how
the agent is doing.

- **Where calls end up** — answered, verified, filed, tow dispatched. Each stage a
  subset of the one above it.
- **Where Ivy needed a second attempt** — every time the claim webhook refused a
  filing and sent her back to ask. The tallest bar is the question she skips most,
  which is the one to fix in the prompt.
- Risk bands, incident mix, how calls arrived, identity pass rate.

**Export JSONL** hands you the whole call log — transcript, tool calls and outcome
per line — shaped for an eval set or a fine-tune rather than a spreadsheet.

The charts are labelled horizontal bars because every question on that page is a
magnitude comparison with long category names. Values are direct-labelled on every
row, so the charts double as their own table and nothing is carried by colour
alone. Ordered scales (funnel stages, risk bands) use a single-hue ordinal ramp;
the categorical charts use three fixed hues. Both palettes were checked with a
validator against this app's actual dark surface rather than eyeballed — the risk
colours the tables use for tags failed the normal-vision separation floor as
chart fills (red and orange sit ΔE 10.6 apart, under the 15 floor), which is
another reason risk is drawn as one ramp rather than four status hues.

## Putting Ivy on a real phone number

Twilio passes inbound calls straight to AssemblyAI over SIP. There is no media
server, no audio bridge and no webhook of ours in the call path.

1. Buy a number in the Twilio console.
2. Add to `.env`:

```sh
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+15551234567
TWILIO_TRUNK_DOMAIN=claimvoice.pstn.twilio.com   # you pick the first part
```

3. Publish with a **public** URL — a phone call has no browser, so the tools must
   be reachable from the internet. A tunnel works for testing; a deploy is better.

```sh
python manage.py publish_agent --public-url https://claimvoice.onrender.com
python manage.py connect_phone
```

```
Trunk: TK7a…  (created)
Origination: routed to sip:sip.assemblyai.com
Number: +15551234567 attached to trunk
Registered: +15551234567 imported
Attached: agent 8d70… answers +15551234567
```

Then call it. Every step checks before it creates, so re-running is safe, and
`--detach` unbinds the agent again.

### Handing the caller to a person

Ivy can take a claim. She cannot reassure somebody whose child is in the back
seat, and she should not try. `request_human` is where the machine steps aside.

Twilio owns the PSTN leg, so the live call can be taken back and pointed
somewhere else with one REST call — no media server, still. Ringing down the
rota is then TwiML: dial the first dispatcher with a timeout, and when Twilio
reports no answer it asks what to do next, which is dial the second. That is
the whole queue. No state machine of ours, no polling, and it survives the web
process restarting mid-call.

```sh
python manage.py create_dispatcher --name "Priya R" --email priya@example.com \
    --phone +15550142001 --shifts "mon-fri 08:00-18:00"
```

```sh
# .env
LIVE_TRANSFERS=1
HANDOFF_FALLBACK_NUMBER=+15550142999   # rung when the rota is empty or exhausted
HANDOFF_RING_SECONDS=20                # per phone, before moving on
HANDOFF_MAX_ATTEMPTS=3                 # people, before offering a call back
```

**Creating the first dispatcher switches the desk login from one shared
password to per-person accounts.** That is the point — a claim closed by
"Dispatcher" answers no question worth asking six months later — but everyone
else needs an account before they can get back in.

A rota with nothing in it means the desk is always open, so an existing
deployment does not discover at 2am that it has silently gone dark. Once
somebody writes a shift, shifts govern. `fri 22:00-06:00` is the night shift,
not an empty set. Somebody with no phone number on file is on the board but
never rung, because a silent leg in the escalation is worse than not being in
it at all.

Without `LIVE_TRANSFERS` nothing is dialled: the handoff is still recorded, the
**Waiting** tab on the board still lights up, and a dispatcher presses *Take
this call* and rings them back. What Ivy says is true either way — she offers a
call back rather than telling somebody to hold for a transfer that is not
coming.

When the rota is empty or everybody has been tried, the caller is told so in
words and the fallback number is texted. A queue that overflows into silence is
not a queue.

Two things to know: the trunk takes ownership of the number, so any Voice webhook
set on the number itself stops applying; and Twilio bills the inbound minutes
while AssemblyAI bills the session, so a live number draws on both accounts.

### Other commands

```sh
python manage.py publish_agent --public-url https://your-app.example.com
python manage.py publish_agent --from-file      # discard the saved profile, use agent.json
python manage.py publish_agent --dry-run        # print what would be published
python manage.py seed_policies                  # the book of policyholders
python manage.py seed_claims --count 12 --clear # claims, each with the call it came from
python manage.py sync_calls [--prune]           # pull session metadata; --prune drops empty call rows
python manage.py connect_phone [--detach]       # attach the agent to a Twilio number
python manage.py create_dispatcher              # somebody who can log in and be rung
python manage.py test
node tools/check_live.mjs                       # the page heartbeat, without a browser
```

## How it fits together

```
browser ──mic──▶ wss://agents.assemblyai.com ──log_claim──▶ /api/log-claim/
   ▲                    (STT · LLM · TTS)                        │
   └────────────── "Claim CV-00018 logged, tow on its way" ◀──────┘
                                                                 │
                                        risk score, tow decision, row on the board
```

The browser holds the audio and nothing else. It mints a 60-second token from
`/api/token/` — the API key never leaves the server — and streams 24 kHz PCM16
over one websocket. AssemblyAI runs the whole pipeline and calls the webhook
itself, which is why the agent behaves identically on a phone number.

`agent.json` is the agent: persona, greeting, voice, turn taking and the
`log_claim` schema. It is the seed for the `AgentProfile` row that `/settings/`
edits, so the file stays a readable default you can commit and reset to.

### How the pages stay current

Every open page polls one endpoint, `/api/pulse/`: a few counts and max ids, a
few hundred bytes, a fixed handful of aggregates however much is on the board.
Each widget names the counters it depends on, and only refetches its own feed
when one of them moves — so a quiet dispatcher board costs a heartbeat rather
than three feeds every two seconds. The heartbeat stops when the tab is hidden,
backs off when the server is unreachable, and catches up the moment the tab
comes back. `node tools/check_live.mjs` proves all four without a browser.

## The one API constraint that bites

**Every tool property must be something the model copies or picks. A property
it has to compose — "one sentence describing what happened, in your own words" —
stops the tool firing at all.** No `session.error`, no failed call: the model
says "let me get that filed for you" and nothing happens, forever.

Bisected against the live API, one property at a time:

| tool schema | `tool.call` fires |
| --- | --- |
| four required fields only | yes |
| `+ caller_name` (copied from the caller) | yes |
| `+ injuries_reported` (a boolean) | yes |
| `+ vehicle` (copied) | yes |
| `+ severity` (an enum it picks from) | yes |
| `+ description` ("one sentence in your own words") | **no** |
| the same field renamed, or reworded tersely | **no** |

So `description` is not in the schema. `severity` is an enum instead, and the
readable line on the dashboard is assembled in `views.summarise()` from the
facts the agent did send. `AgentProfileTests.test_tool_schema_asks_the_model_to_
compose_nothing` fails the build if a prose field creeps back in.

Two smaller ones found the same way:

- **Client-side function tools are dropped from stored agents.** Publish a tool
  with no `http` block and it comes back with `http: null`, invisible to the
  model. AssemblyAI stores HTTP tools only, so the webhook needs a public URL —
  there is no local-only mode. `/settings/` says so rather than letting you talk
  into a dead end.
- **A client that stops sending audio stalls turn detection.** The end of a turn
  is measured in silence, so silence has to keep arriving. Both the browser
  client and the simulator pump frames continuously.

## Risk scoring

`claims/risk.py`, deliberately a readable rule set rather than a model: a base
per incident type, plus points for undrivable, injuries, the reported severity,
and keywords in the location. Every rule contributes a reason, and the reasons
are what the dashboard shows under the score. Theft never dispatches a tow.

## The drivability guardrail

Drivability decides whether a tow rolls, so it has to come from the caller, not
from the model's read of the damage. Told only "never infer it", the model
inferred it anyway on three runs out of three — "the airbags went off" became
`is_drivable: false` without anyone being asked.

A required boolean gives it no way to say *I have not asked*, so it guesses. The
fix is to give it one: `is_drivable` is an enum of `yes`, `no`, `not_asked`, and
the webhook refuses `not_asked` and sends back the question to ask. It works
because it stops relying on the prompt holding:

```
 64.6s  tool  log_claim({..., "is_drivable": "not_asked"})
 71.9s  ivy   I just need to check one more thing before I can file this.
               Is the Honda Civic still driveable?
 78.5s  tool  log_claim({..., "is_drivable": "no"})
```

The payload still stores a boolean; the enum only exists so the model has an
honest answer available.

## The webhooks

`POST /api/verify/` and `POST /api/log-claim/`, both csrf exempt, both optionally
requiring `X-Claim-Secret`.

```json
{
  "policy_number": "PV482193",
  "incident_type": "collision",
  "location": "I-95 northbound near exit 12",
  "is_drivable": false,
  "injuries_reported": false,
  "caller_name": "Dana Whitfield",
  "vehicle": "silver Toyota Camry",
  "severity": "airbags_deployed"
}
```

It answers with a line written to be spoken:

```json
{
  "ok": true,
  "message": "Claim CV-00018 logged. A tow truck is being dispatched to I-95 northbound near exit 12 with an estimated arrival of 26 minutes.",
  "claim_reference": "CV-00018",
  "risk_score": 71,
  "priority": "high"
}
```

Two things worth knowing:

- **Bad payloads answer 200.** A 4xx body the model cannot parse leaves the
  caller in silence; the `message` is what gets spoken either way, and `ok`
  carries the real status.
- **Repeat calls are idempotent for ten minutes.** The same policy and incident
  replays the first claim, with the same reference and the same tow ETA. An
  agent that files twice must not become two tow trucks.

## Deploying

`render.yaml` is a one-click blueprint — a free web service plus a free Postgres.
Render prompts for `ASSEMBLYAI_API_KEY` and generates the rest, including a
webhook secret the published tools then carry.

1. Push the repo, then **New > Blueprint** on Render and point it at the repo.
2. Paste your AssemblyAI key when prompted.
3. When it is live, open `/settings/` and press **Save & publish**. The public
   base URL is already filled in from the service's own hostname, and publishing
   points both tool webhooks at production.

Migrations and the policy seed run in the build command rather than a
`preDeployCommand`, because pre-deploy commands are a paid feature and would be
skipped in silence on the free plan.

`Procfile` covers Railway and Heroku. SQLite by default, Postgres when
`DATABASE_URL` is set.

### The free plan and phone calls

A free Render service sleeps after about fifteen minutes idle and takes roughly
fifty seconds to wake. That is survivable in a browser — you press Start and
wait — but it breaks a phone call: the tool webhook times out while the caller
listens to nothing, and the claim is never filed.

If a phone number needs to answer reliably, either move the web service to a
paid instance, or keep the free one awake by pinging `/healthz/` every ten
minutes from an external scheduler. The health check is cheap and touches only
the database.

Anyone with the URL can start sessions billed to your API key.
