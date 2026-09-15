#!/usr/bin/env node
/* Generate the explainer video's narration with AssemblyAI's own TTS.
 *
 *   node tools/generate_narration.mjs
 *
 * AssemblyAI has no standalone text-to-speech endpoint — TTS only exists
 * inside the live Voice Agent websocket, the same pipeline Ivy runs on. So
 * this publishes a second, throwaway agent (distinct from Ivy and Rae) whose
 * only job is to speak a `greeting` verbatim the moment a session opens,
 * updates that one greeting between lines, and captures the reply audio the
 * same way the browser client and `simulate_call.mjs` already do — 24 kHz
 * PCM16 frames off `reply.audio` — then deletes the agent when it's done.
 *
 * Needs ASSEMBLYAI_API_KEY in .env. Does not need the Django server running:
 * token minting and agent publishing both go straight to AssemblyAI.
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const WIRE_RATE = 24000

function loadEnv() {
  const text = readFileSync(join(ROOT, '.env'), 'utf8')
  const env = {}
  for (const line of text.split('\n')) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$/)
    if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, '')
  }
  return env
}

const env = loadEnv()
const API_KEY = process.env.ASSEMBLYAI_API_KEY || env.ASSEMBLYAI_API_KEY
if (!API_KEY) throw new Error('ASSEMBLYAI_API_KEY not found in .env')
const API_BASE = 'https://agents.assemblyai.com/v1'
const VOICE_ID = process.env.NARRATOR_VOICE || 'george' // American — distinct from Ivy (anna) and Rae

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(API_BASE + path, {
    method,
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`${method} ${path} failed (${res.status}): ${text}`)
  return text ? JSON.parse(text) : {}
}

const NARRATOR_CONFIG = (greeting) => ({
  name: 'ClaimVoice Narrator (video)',
  system_prompt:
    'You narrate a product video and never take a live call. Say nothing beyond the greeting; if anything is heard from the other side, stay silent.',
  greeting,
  voice: { voice_id: VOICE_ID },
  input: {
    keyterms: [],
    turn_detection: {
      vad_threshold: 0.45,
      min_silence: 700,
      max_silence: 2600,
      interrupt_response: false,
    },
  },
  output: { voice: VOICE_ID, volume: 100 }, // 0-100 scale, not 0-1 — AgentProfile.volume defaults to 100
  tools: [],
})

async function publishNarrator(greeting) {
  const listing = await api('/agents')
  const existing = (listing.agents || []).find((a) => a.name === 'ClaimVoice Narrator (video)')
  if (existing) {
    await api(`/agents/${existing.id}`, { method: 'PUT', body: NARRATOR_CONFIG(greeting) })
    return existing.id
  }
  const created = await api('/agents', { method: 'POST', body: NARRATOR_CONFIG(greeting) })
  return created.id
}

async function updateGreeting(agentId, greeting) {
  await api(`/agents/${agentId}`, { method: 'PUT', body: NARRATOR_CONFIG(greeting) })
}

async function mintToken() {
  const res = await api('/token?product=voice_agent&expires_in_seconds=60')
  return res.token
}

function pcmToWav(pcm, rate) {
  const buffer = Buffer.alloc(44 + pcm.length)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + pcm.length, 4)
  buffer.write('WAVE', 8)
  buffer.write('fmt ', 12)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(rate, 24)
  buffer.writeUInt32LE(rate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(pcm.length, 40)
  pcm.copy(buffer, 44)
  return buffer
}

// Capture one greeting's audio over a fresh session. The agent speaks first
// and unprompted, so nothing needs to be sent — just listened for.
function captureGreeting(agentId, { timeoutMs = 25000 } = {}) {
  return new Promise((resolve, reject) => {
    mintToken().then((token) => {
      const ws = new WebSocket(`wss://agents.assemblyai.com/v1/ws?token=${token}`)
      const chunks = []
      let done = false
      const timer = setTimeout(() => {
        if (!done) {
          done = true
          ws.close()
          reject(new Error('timed out waiting for the greeting'))
        }
      }, timeoutMs)

      ws.addEventListener('open', () => {
        ws.send(JSON.stringify({ type: 'session.update', session: { agent_id: agentId } }))
      })
      ws.addEventListener('message', ({ data }) => {
        const msg = JSON.parse(data)
        if (msg.type === 'reply.audio') {
          chunks.push(Buffer.from(msg.data, 'base64'))
        } else if (msg.type === 'reply.done') {
          if (!done) {
            done = true
            clearTimeout(timer)
            ws.close()
            resolve(Buffer.concat(chunks))
          }
        } else if (msg.type === 'error') {
          if (!done) {
            done = true
            clearTimeout(timer)
            ws.close()
            reject(new Error(`agent error: ${JSON.stringify(msg)}`))
          }
        }
      })
      ws.addEventListener('error', (err) => {
        if (!done) {
          done = true
          clearTimeout(timer)
          reject(err)
        }
      })
    }, reject)
  })
}

const LINES = [
  {
    index: 0,
    text: 'When a driver crashes, the call center is the worst version of that day. Hold music, a script read at them, and a second call just to get a truck moving.',
  },
  {
    index: 1,
    text: "ClaimVoice's Ivy answers instead. Built on AssemblyAI's voice agent platform. Speech to understanding to speech, in one live call.",
  },
  {
    index: 2,
    text: "She won't say a name until she's sure who's calling. A policy number, the last four digits, checked before anything else.",
  },
  {
    index: 3,
    text: "As the driver talks, Ivy fills in what happened. And when she doesn't know something, she asks, instead of guessing.",
  },
  {
    index: 4,
    text: 'The moment the call ends, a risk score is calculated, a tow is dispatched, and the claim is already sitting on the dispatcher’s board.',
  },
  {
    index: 5,
    text: 'And when a caller needs a person instead of an agent, Ivy hands off. Never a silent dead end.',
  },
  {
    index: 6,
    text: 'Two hundred and one tests. Free to deploy. Built end to end on AssemblyAI. This is ClaimVoice.',
  },
]

async function main() {
  console.log(`narrator voice: ${VOICE_ID}`)
  const agentId = await publishNarrator(LINES[0].text)
  console.log(`agent published: ${agentId}`)

  for (const line of LINES) {
    process.stdout.write(`line ${line.index}: `)
    if (line.index > 0) await updateGreeting(agentId, line.text)
    // AssemblyAI needs a moment to pick up a republished greeting.
    await new Promise((r) => setTimeout(r, 1500))
    let pcm
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        pcm = await captureGreeting(agentId)
        break
      } catch (err) {
        if (attempt === 3) throw err
        console.log(`\n  retry ${attempt} after: ${err.message}`)
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    const wav = pcmToWav(pcm, WIRE_RATE)
    const outPath = join(ROOT, 'video', 'public', 'audio', `raw_line${line.index}.wav`)
    writeFileSync(outPath, wav)
    console.log(`${(pcm.length / 2 / WIRE_RATE).toFixed(1)}s -> ${outPath}`)
  }

  await api(`/agents/${agentId}`, { method: 'DELETE' })
  console.log('narrator agent cleaned up')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
