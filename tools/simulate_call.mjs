#!/usr/bin/env node
/* Drive a whole call without a microphone.
 *
 *   node tools/simulate_call.mjs
 *   CLAIMVOICE_URL=http://localhost:8000 node tools/simulate_call.mjs
 *
 * Synthesises a caller with macOS `say`, streams them to the agent as 24 kHz
 * PCM16, answers log_claim against the local Django webhook, and prints the
 * transcript. It proves the whole loop before a demo, when nobody wants to
 * talk to a laptop, and it exits non-zero if the claim never gets filed.
 *
 * The caller is a fact sheet plus a router, not a script: it answers whatever
 * Ivy actually asked, in whatever order she asks it. A fixed script desyncs
 * the moment the agent chooses a different question.
 *
 * Requires the Django server to be running; it mints the session token.
 */

import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = (process.env.CLAIMVOICE_URL || 'http://localhost:8000').replace(/\/$/, '')
const WIRE_RATE = 24000
const FRAME_MS = 20
const FRAME_SAMPLES = (WIRE_RATE / 1000) * FRAME_MS
const FRAME_BYTES = FRAME_SAMPLES * 2
// Silence the caller leaves after answering. The agent commits a turn after
// min_silence (700 ms), so this is enough to end a turn without dead air.
const TAIL_SILENCE_MS = 1200
const VOICE = process.env.SAY_VOICE || 'Samantha'

// The caller's answers, keyed by what they answer. The router picks one per
// question; `intro` opens the call and `fallback` covers anything unmatched.
// Defaults to Dana Whitfield from `manage.py seed_policies`. Override with
// POLICY / LAST4 / CALLER_NAME to call in as someone else.
const POLICY = process.env.POLICY || 'PV482193'
const LAST4 = process.env.LAST4 || '2887'
const spaced = (text) => String(text).split('').join(' ')

const CALLER = {
  safety: "Yes, I'm safe. I pulled over onto the shoulder.",
  injuries: "No, nobody is hurt. We are both fine, just a bit shaken up.",
  policy: `My policy number is ${spaced(POLICY)}.`,
  last4: `The last four digits are ${spaced(LAST4)}.`,
  wrong4: `The last four digits are ${spaced(process.env.WRONG4 || '1111')}.`,
  incident: 'Someone rear ended me on the highway and the airbags went off.',
  location: "I'm on interstate 95 northbound, just past exit 12.",
  drivable: "No, it's not drivable. The back end is crushed and it is leaking.",
  name: `My name is ${process.env.CALLER_NAME || 'Dana Whitfield'}.`,
  fallback: "Sorry, could you say that again? I'm a bit shaken up.",
}

// Matched against Ivy's last line, first hit wins, so the more specific
// patterns come first.
const ROUTES = [
  // The identity check comes first, so its patterns are matched first.
  [/last four|last 4|final four|four digits|phone number/i, 'last4'],
  [/injur|hurt|anyone (?:ok|okay|hurt)|everyone (?:ok|okay)|medical|ambulance/i, 'injuries'],
  [/policy\s*number|policy/i, 'policy'],
  [/drivab|drive (?:it|the car)|still driv|tow/i, 'drivable'],
  [/where|location|street|road|address|exit|mile marker|cross street/i, 'location'],
  [/what happened|describe|tell me (?:what|more)|kind of|type of (?:incident|accident)/i, 'incident'],
  [/your name|who am i speaking|may i (?:take|have) your name/i, 'name'],
  [/safe|out of traffic|somewhere safe/i, 'safety'],
]

// If Ivy asks something unmatched, work through anything not yet said rather
// than repeating the fallback until the call times out.
const ORDER = ['safety', 'policy', 'last4', 'injuries', 'incident', 'location', 'drivable', 'name']

const work = mkdtempSync(join(tmpdir(), 'claimvoice-'))
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// The same stereo recording the browser page makes: caller left, Ivy right,
// aligned on the caller's clock because the mic side runs in real time.
class Recorder {
  constructor(rate = WIRE_RATE) {
    this.rate = rate
    this.left = []
    this.right = []
    this.leftLen = 0
    this.rightLen = 0
  }
  get position() {
    return this.leftLen
  }
  addCaller(int16) {
    this.left.push(int16)
    this.leftLen += int16.length
  }
  addAgent(int16) {
    this.right.push(int16)
    this.rightLen += int16.length
  }
  alignAgent(position) {
    if (position > this.rightLen) {
      this.right.push(new Int16Array(position - this.rightLen))
      this.rightLen = position
    }
  }
  truncateAgent(position) {
    // Dropping whole chunks is close enough: the point is that the tail the
    // caller never heard does not end up in the recording.
    while (this.rightLen > position && this.right.length) {
      const last = this.right[this.right.length - 1]
      if (this.rightLen - last.length < position) break
      this.right.pop()
      this.rightLen -= last.length
    }
  }
  _flat(chunks, length) {
    const out = new Int16Array(length)
    let at = 0
    for (const chunk of chunks) {
      out.set(chunk, at)
      at += chunk.length
    }
    return out
  }
  toWav() {
    const frames = Math.max(this.leftLen, this.rightLen)
    if (!frames) return null
    const left = this._flat(this.left, this.leftLen)
    const right = this._flat(this.right, this.rightLen)
    const buffer = Buffer.alloc(44 + frames * 4)
    buffer.write('RIFF', 0)
    buffer.writeUInt32LE(36 + frames * 4, 4)
    buffer.write('WAVE', 8)
    buffer.write('fmt ', 12)
    buffer.writeUInt32LE(16, 16)
    buffer.writeUInt16LE(1, 20)
    buffer.writeUInt16LE(2, 22)
    buffer.writeUInt32LE(this.rate, 24)
    buffer.writeUInt32LE(this.rate * 4, 28)
    buffer.writeUInt16LE(4, 32)
    buffer.writeUInt16LE(16, 34)
    buffer.write('data', 36)
    buffer.writeUInt32LE(frames * 4, 40)
    for (let i = 0; i < frames; i++) {
      buffer.writeInt16LE(i < this.leftLen ? left[i] : 0, 44 + i * 4)
      buffer.writeInt16LE(i < this.rightLen ? right[i] : 0, 46 + i * 4)
    }
    return buffer
  }
}

let clipIndex = 0
const clips = new Map()

function speak(text) {
  if (clips.has(text)) return clips.get(text)
  const aiff = join(work, `turn${clipIndex}.aiff`)
  const wav = join(work, `turn${clipIndex++}.wav`)
  execFileSync('say', ['-v', VOICE, '-o', aiff, text])
  execFileSync('afconvert', ['-f', 'WAVE', '-d', `LEI16@${WIRE_RATE}`, '-c', '1', aiff, wav])
  const buffer = readFileSync(wav)
  // Find the data chunk rather than assuming a 44 byte header.
  let offset = 12
  while (offset < buffer.length - 8) {
    const id = buffer.toString('ascii', offset, offset + 4)
    const size = buffer.readUInt32LE(offset + 4)
    if (id === 'data') {
      const pcm = buffer.subarray(offset + 8, offset + 8 + size)
      clips.set(text, pcm)
      return pcm
    }
    offset += 8 + size + (size % 2)
  }
  throw new Error('no data chunk in ' + wav)
}

async function main() {
  const tokenRes = await fetch(`${BASE}/api/token/`)
  if (!tokenRes.ok) {
    throw new Error(`token request failed (${tokenRes.status}) — is the server running at ${BASE}?`)
  }
  const { token } = await tokenRes.json()

  const agentId = process.env.AGENT_ID || (await agentIdFromHealth())
  if (!agentId) throw new Error('no agent published — run: python manage.py publish_agent')

  console.log(`agent   ${agentId}`)
  console.log(`caller  "${VOICE}" as ${POLICY} / ${LAST4} via ${BASE}\n`)

  const ws = new WebSocket(`wss://agents.assemblyai.com/v1/ws?token=${token}`)
  const started = Date.now()
  // Anything filed before this instant belongs to an earlier call.
  const callOpenedAt = new Date(started - 5000).toISOString()
  const at = () => ((Date.now() - started) / 1000).toFixed(1).padStart(5) + 's'

  // The caller's outgoing audio. The pump below drains it at real time speed
  // and sends silence whenever it is empty: a client that stops sending audio
  // entirely leaves the agent's turn detector waiting for the next frame.
  let queue = []
  let live = false
  let closing = false
  const SILENCE = Buffer.alloc(FRAME_BYTES)

  const say = (text) => {
    const pcm = speak(text)
    for (let offset = 0; offset < pcm.length; offset += FRAME_BYTES) {
      queue.push(pcm.subarray(offset, offset + FRAME_BYTES))
    }
    for (let ms = 0; ms < TAIL_SILENCE_MS; ms += FRAME_MS) queue.push(SILENCE)
  }

  async function pump() {
    while (ws.readyState === 1) {
      if (live) {
        const frame = queue.shift() || SILENCE
        recorder.addCaller(
          new Int16Array(frame.buffer, frame.byteOffset, frame.length / 2)
        )
        ws.send(JSON.stringify({ type: 'input.audio', audio: frame.toString('base64') }))
      }
      await sleep(FRAME_MS)
    }
  }

  // --- the caller's head ---
  const said = new Set()

  // Ivy's line usually recaps what she already has before asking the next
  // thing, and the recap is full of words the routes match on. Only the last
  // question in the line is the question.
  function question(text) {
    const asked = text.match(/[^.!?]*\?/g)
    return asked ? asked[asked.length - 1] : text
  }

  // WRONG_FIRST=1 fluffs the identity check once, to exercise the recovery
  // path: Ivy should refuse to disclose anything and ask again.
  let missedOnce = !process.env.WRONG_FIRST

  function answer(text) {
    // "Let me get this filed for you" is not a question; talking over it
    // barges in and cancels the tool call.
    if (/\b(filed|filing|one moment|bear with me|hold on)\b/i.test(text) && !text.includes('?')) {
      return null
    }
    let key = ROUTES.find(([pattern]) => pattern.test(question(text)))?.[1]
    if (key === 'last4' && !missedOnce) {
      missedOnce = true
      return CALLER.wrong4
    }
    // Ivy re-asking something already answered means she did not catch it, so
    // repeat it; anything unrecognised moves to the next unsaid fact.
    if (!key) key = ORDER.find((k) => !said.has(k)) || null
    // Everything is on the table and she asked nothing: let her work.
    if (!key) return null
    said.add(key)
    return CALLER[key]
  }

  let pending = null
  // The call record, posted the same way the browser page posts one, so a
  // simulated call shows up on the board with its transcript like any other.
  let assemblySessionId = null
  const recorder = new Recorder()
  const turns = []
  const toolCalls = []
  const note = (role, text) =>
    turns.push({ role, text, at: Number(((Date.now() - started) / 1000).toFixed(1)) })

  let recordTimer = null

  async function postRecord(ended) {
    if (!assemblySessionId) return
    try {
      await fetch(`${BASE}/api/conversations/ingest/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: assemblySessionId,
          channel: 'simulator',
          agent_id: agentId,
          turns,
          tool_calls: toolCalls,
          ended: Boolean(ended),
          close_reason: ended || '',
          duration_seconds: (Date.now() - started) / 1000,
        }),
      })
    } catch {
      /* the call matters more than the record of it */
    }
  }

  let sawToolCall = false
  let claimReference = null
  let filed = null

  const done = new Promise((resolve) => ws.addEventListener('close', resolve))

  ws.addEventListener('open', () => {
    ws.send(JSON.stringify({ type: 'session.update', session: { agent_id: agentId } }))
  })

  ws.addEventListener('message', async ({ data }) => {
    const msg = JSON.parse(data)
    switch (msg.type) {
      case 'reply.started':
        recorder.alignAgent(recorder.position)
        break

      case 'reply.audio': {
        const pcm = Buffer.from(msg.data, 'base64')
        recorder.addAgent(new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length / 2))
        break
      }

      case 'input.speech.started':
        recorder.truncateAgent(recorder.position)
        break

      case 'session.ready':
        console.log(`${at()}  --    session ready`)
        assemblySessionId = msg.session_id || null
        // Register the call before any tool can fire: the webhook identifies
        // its call by finding the one that is currently open.
        postRecord(false)
        recordTimer = setInterval(() => postRecord(false), 5000)
        live = true
        pump()
        break

      case 'transcript.agent': {
        console.log(`${at()}  ivy   ${msg.text}`)
        note('agent', msg.text)
        // Once the claim is filed the caller has nothing left to answer, and
        // answering anyway is what talks the agent into filing a second time.
        if (closing || filed) break
        pending = answer(msg.text)
        break
      }

      // The reply's audio has finished streaming, so it is the caller's turn.
      // Answering off transcript.agent instead talks over her, and the API
      // reads that as barge-in and abandons whatever she was doing.
      case 'reply.done': {
        // Ivy has read the reference back, so hang up rather than sit on the
        // line racking up session minutes.
        if (filed && !closing) {
          closing = true
          setTimeout(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: 'session.end' })), 1200)
          break
        }
        if (closing || !pending) break
        const line = pending
        pending = null
        console.log(`${at()}  you   ${line}`)
        note('caller', line)
        say(line)
        break
      }

      // log_claim is an http tool, so AssemblyAI posts it to Django itself.
      // This is the notification; answering it here would file the claim twice.
      case 'tool.call': {
        // Never print the digits the caller just used to prove who they are.
        const shown = msg.name === 'verify_policyholder'
          ? { ...msg.arguments, phone_last4: '••••' }
          : msg.arguments
        console.log(`${at()}  tool  ${msg.name}(${JSON.stringify(shown)})`)
        toolCalls.push({
          name: msg.name,
          arguments: shown,
          at: Number(((Date.now() - started) / 1000).toFixed(1)),
        })
        note('tool', `${msg.name}(${JSON.stringify(shown)})`)
        if (msg.name !== 'log_claim') break
        sawToolCall = true
        const landed = await waitForClaim(msg.arguments?.policy_number, callOpenedAt, 8)
        if (landed) {
          filed = landed
          claimReference = `CV-${String(landed.id).padStart(5, '0')}`
          console.log(`${at()}  db    ${claimReference} risk ${landed.risk_score} (${landed.priority})` +
            `${landed.tow_required ? ', tow dispatched' : ''}`)
        } else {
          // The webhook rejected it — usually a field the agent filed without
          // asking for. It gets told what to ask, so the call carries on.
          console.log(`${at()}  db    rejected, waiting for Ivy to ask again`)
        }
        break
      }

      case 'session.error':
        console.error(`${at()}  err   ${msg.code}: ${msg.message}`)
        break

      case 'session.ended':
        ws.close()
        break
    }
  })

  // A stuck call should not hang a CI run.
  const budget = Number(process.env.CALL_BUDGET_S || 180) * 1000
  const guard = setTimeout(() => {
    console.error(`\ntimed out after ${budget / 1000}s`)
    ws.close()
  }, budget)

  await done
  clearTimeout(guard)
  clearInterval(recordTimer)
  await postRecord('client_end')

  // The transcript post creates the row this attaches to, so it goes first.
  const wav = recorder.toWav()
  if (wav && assemblySessionId) {
    const form = new FormData()
    form.append('session_id', assemblySessionId)
    form.append('audio', new Blob([wav], { type: 'audio/wav' }), 'call.wav')
    try {
      const res = await fetch(`${BASE}/api/recordings/upload/`, { method: 'POST', body: form })
      const body = await res.json()
      console.log(
        res.ok
          ? `        recording ${(body.bytes / 1048576).toFixed(1)} MB uploaded`
          : `        recording rejected: ${body.error}`
      )
    } catch (error) {
      console.log(`        recording upload failed: ${error.message}`)
    }
  }
  rmSync(work, { recursive: true, force: true })

  console.log('')
  if (filed) {
    console.log(
      `filed ${claimReference} · ${filed.incident_label} · ${filed.location} · ` +
        `risk ${filed.risk_score} (${filed.priority}) — see ${BASE}/dashboard/`
    )
    process.exit(0)
  }
  console.error(
    sawToolCall
      ? 'log_claim fired but no claim reached the database — is the webhook URL reachable?'
      : 'the agent never called log_claim'
  )
  process.exit(1)
}

// The webhook fires from AssemblyAI's side, so the row appears a beat after
// the tool call. Poll our own API for it rather than assuming.
//
// `since` is what makes this correct: a caller who has claimed before already
// has rows under that policy number, and matching one of those reports a claim
// that was never filed — and, worse, ends the call while the agent is still
// asking questions.
async function waitForClaim(policyNumber, since, tries = 15) {
  const wanted = policyNumber ? String(policyNumber).toUpperCase().replace(/\s/g, '') : null
  const after = new Date(since).getTime()
  for (let i = 0; i < tries; i++) {
    await sleep(1000)
    try {
      const res = await fetch(`${BASE}/api/claims/`)
      const { claims } = await res.json()
      const fresh = claims.filter((c) => new Date(c.created_at).getTime() >= after)
      const match = wanted ? fresh.find((c) => c.policy_number === wanted) : fresh[0]
      if (match) return match
    } catch {
      /* the call is still worth finishing */
    }
  }
  return null
}

async function agentIdFromHealth() {
  try {
    const res = await fetch(`${BASE}/healthz/`)
    return (await res.json()).agent_id
  } catch {
    return null
  }
}

main().catch((error) => {
  console.error(error.message)
  rmSync(work, { recursive: true, force: true })
  process.exit(1)
})
