"""Keep the verification digits out of the record.

Ivy asks for the last four digits of the phone number on the policy, and the
caller says them out loud. That answer is the shared secret the whole identity
check rests on, so storing it is storing the key next to the lock: a transcript
or a recording that leaks is enough to impersonate the caller on the next call.

The tool arguments were already masked. The spoken answer was not — it sat in
`turns` as text and stayed audible in every WAV. This module removes both.

What it does not do is guess. Only a caller turn that answers a question about
the digits is touched, and only the digits inside it, so the transcript still
reads as a conversation and still shows whether Ivy asked the question at all.
"""

import io
import re
import wave

# "8 4 3 2", "8-4-3-2", "8432" — a run of single digits however they were
# transcribed. Three or more, so a lone house number survives.
DIGIT_RUN = re.compile(r"\b\d(?:[\s\-–.]*\d){2,}\b")

# What the agent asks just before the answer we care about.
ASKED_FOR_DIGITS = re.compile(
    r"last\s*(?:four|4)|final\s*(?:four|4)|four\s*digits|digits\s*of\s*the\s*phone"
    r"|phone\s*number\s*on\s*the\s*(?:policy|account)",
    re.I,
)
# The caller often repeats the cue themselves.
SAID_DIGITS = re.compile(r"last\s*(?:four|4)|digits\s+are|final\s*(?:four|4)", re.I)

MASK = "••••"

# How much audio to blank around the moment a turn was stamped. A turn is
# timestamped when it was finalised, so the speech runs backwards from there;
# the tail is padding for the stamp landing a beat early.
LEAD_SECONDS = 6.0
TAIL_SECONDS = 0.8


def _mask_digits(text):
    """Replace digit runs, and say how many were taken."""
    count = 0

    def swap(match):
        nonlocal count
        count += 1
        return MASK

    return DIGIT_RUN.sub(swap, text), count


def redact_turns(turns, known_last4=""):
    """Return (turns, spans) with the spoken verification digits removed.

    `spans` are the windows of caller audio that carried them, for the recording
    to blank in turn.
    """
    if not turns:
        return turns, []

    cleaned = []
    spans = []
    agent_asked = False

    for turn in turns:
        role = turn.get("role")
        text = turn.get("text") or ""

        if role == "agent":
            agent_asked = bool(ASKED_FOR_DIGITS.search(text))
            cleaned.append(turn)
            continue

        if role != "caller":
            cleaned.append(turn)
            continue

        # A caller turn counts when the agent just asked, when the caller
        # framed it themselves, or when it simply contains the known secret.
        holds_secret = bool(
            known_last4 and re.search(rf"\b{re.escape(known_last4)}\b", re.sub(r"[\s\-–.]", "", text))
        )
        if not (agent_asked or SAID_DIGITS.search(text) or holds_secret):
            cleaned.append(turn)
            continue

        masked, count = _mask_digits(text)
        agent_asked = False
        if not count:
            cleaned.append(turn)
            continue

        at = float(turn.get("at") or 0)
        spans.append(
            {
                "start": max(0.0, round(at - LEAD_SECONDS, 2)),
                "end": round(at + TAIL_SECONDS, 2),
                "reason": "verification_digits",
            }
        )
        copy = dict(turn)
        copy["text"] = masked
        copy["redacted"] = True
        cleaned.append(copy)

    return cleaned, spans


def redact_tool_calls(tool_calls):
    """Belt and braces: the client masks these, but a phone call's tool row is
    written server-side from whatever arrived."""
    if not tool_calls:
        return tool_calls
    out = []
    for entry in tool_calls:
        arguments = entry.get("arguments")
        if isinstance(arguments, dict) and arguments.get("phone_last4"):
            entry = {**entry, "arguments": {**arguments, "phone_last4": MASK}}
        out.append(entry)
    return out


def blank_spans(wav_bytes, spans, channel=0):
    """Silence one channel of a WAV across the given windows.

    Only the caller's channel is touched — Ivy never says the digits, and
    leaving her side intact means you can still hear the question being asked,
    which is the part worth reviewing.
    """
    if not spans or not wav_bytes:
        return wav_bytes

    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = bytearray(source.readframes(source.getnframes()))
        params = source.getparams()

    if width != 2 or channel >= channels:
        return wav_bytes

    stride = channels * width
    total = len(frames) // stride
    for span in spans:
        first = max(0, int(span["start"] * rate))
        last = min(total, int(span["end"] * rate))
        for frame in range(first, last):
            at = frame * stride + channel * width
            frames[at] = 0
            frames[at + 1] = 0

    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setparams(params)
        sink.writeframes(bytes(frames))
    return out.getvalue()
