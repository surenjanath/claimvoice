#!/usr/bin/env bash
# Bring the whole demo up locally: tunnel, agent, server.
#
#   ./run.sh
#
# AssemblyAI has to reach the webhook from the internet, so this opens a
# cloudflared quick tunnel, publishes the agent pointed at that URL, and prints
# the link to share. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

command -v cloudflared >/dev/null || {
  echo "cloudflared is not installed. brew install cloudflared" >&2
  exit 1
}
grep -q '^ASSEMBLYAI_API_KEY=.\+' .env 2>/dev/null || {
  echo "Add ASSEMBLYAI_API_KEY to .env first (see .env.example)." >&2
  exit 1
}

log=$(mktemp -t claimvoice-tunnel)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -f "$log"; }
trap cleanup EXIT INT TERM

"$PY" manage.py migrate --noinput >/dev/null
"$PY" manage.py runserver "$PORT" --noreload >/dev/null 2>&1 &

# A tunnel to a port nothing is listening on fails in a confusing way, so
# check the app first.
for _ in $(seq 1 20); do
  curl -fsS --max-time 2 "http://localhost:$PORT/healthz/" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS --max-time 3 "http://localhost:$PORT/healthz/" >/dev/null || {
  echo "Django did not start on port $PORT. Is it already in use?" >&2
  exit 1
}

# A quick tunnel occasionally comes up with a hostname that never lands in DNS,
# and AssemblyAI refuses a webhook host it cannot resolve. Wait for the app to
# answer through the tunnel; if it never does, throw that tunnel away and take
# another one.
url=""
for try in 1 2; do
  echo "Opening a public tunnel…"
  : >"$log"
  cloudflared tunnel --url "http://localhost:$PORT" >"$log" 2>&1 &
  tunnel_pid=$!

  candidate=""
  for _ in $(seq 1 40); do
    candidate=$(grep -aoE "https://[a-z0-9-]+\\.trycloudflare\\.com" "$log" | head -1 || true)
    [ -n "$candidate" ] && break
    sleep 0.5
  done
  if [ -z "$candidate" ]; then
    kill "$tunnel_pid" 2>/dev/null || true
    continue
  fi

  echo "Waiting for $candidate to answer…"
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "$candidate/healthz/" >/dev/null 2>&1; then
      url="$candidate"
      break
    fi
    sleep 2
  done
  [ -n "$url" ] && break

  echo "That tunnel never answered, taking another."
  kill "$tunnel_pid" 2>/dev/null || true
done
[ -n "$url" ] || { echo "Could not open a working tunnel. See $log" >&2; exit 1; }

for attempt in 1 2 3; do
  "$PY" manage.py publish_agent --public-url "$url" && break
  [ "$attempt" = 3 ] && { echo "Could not publish the agent." >&2; exit 1; }
  echo "Retrying in 10s…"
  sleep 10
done

cat <<INFO

  Talk to Ivy   $url/
  Dispatcher    $url/dashboard/
  Agent config  $url/settings/

  Local         http://localhost:$PORT/
  Simulate a call:  CLAIMVOICE_URL=http://localhost:$PORT node tools/simulate_call.mjs

  Anyone with the link can start a session billed to your API key.
  Ctrl-C to stop.

INFO
wait
