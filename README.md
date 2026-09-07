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
| `/dashboard/` | The dispatcher board. Claims and risk on one tab, every call transcript on the other. |
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

## Every call, kept

The Conversations tab on the dashboard holds the full transcript of each call,
turn by turn with timings, the tool calls inline, and which identity checks
passed. That is the corpus for tuning the prompt: the failures are visible, so
you can see exactly which question Ivy asked badly.

AssemblyAI keeps session metadata but not the words, so the transcript comes from
whatever held the call. The browser page posts turns as they happen; the
simulator does the same. A phone call has no browser, so its record is built from
its tool calls, and `manage.py sync_calls` tops it up with duration and close
reason from the sessions API.

Tool webhooks arrive from AssemblyAI with no session id, so they land on a row of
their own; when the transcript arrives it absorbs any tool-call row from the same
window, and the dispatcher sees one call rather than two halves.

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
python manage.py sync_calls                     # pull session metadata for phone calls
python manage.py connect_phone [--detach]       # attach the agent to a Twilio number
python manage.py test
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

`render.yaml` is a one-click blueprint; `Procfile` covers Railway and Heroku.
Set `ASSEMBLYAI_API_KEY`, and after the first deploy set `PUBLIC_BASE_URL` to
the service origin and run `publish_agent` so the webhook points at production
rather than a tunnel. SQLite by default, Postgres when `DATABASE_URL` is set.

Anyone with the URL can start sessions billed to your API key.
