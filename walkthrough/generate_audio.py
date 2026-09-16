import json
import subprocess
from pathlib import Path

from kokoro import KPipeline
import soundfile as sf

ROOT = Path(__file__).parent
script = json.loads((ROOT / "script.json").read_text())

AUDIO_DIR = ROOT / "video" / "public" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# CUSTOMIZE: lang_code must match the voice's accent family:
#   'a' = American English (af_bella, af_heart, af_nicole, af_sarah, am_adam, am_michael)
#   'b' = British English  (bf_emma, bf_isabella, bm_george, bm_lewis)
# SPEED: 1.0 = natural pace; 1.15-1.25 reads as "a bit faster" without sounding rushed.
pipeline = KPipeline(lang_code="b")
VOICE = "bm_george"
SPEED = 1.18

manifest = {}
durations = {}

for scene in script["scenes"]:
    scene_id = scene["id"]
    text = scene["narration"]
    print(f"--- {scene_id} ---")
    chunks = []
    for gs, ps, audio in pipeline(text, voice=VOICE, speed=SPEED):
        chunks.append(audio)
    import numpy as np

    full = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    wav_path = AUDIO_DIR / f"{scene_id}.wav"
    sf.write(wav_path, full, 24000)

    mp3_path = AUDIO_DIR / f"{scene_id}.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_path)],
        check=True,
        capture_output=True,
    )
    wav_path.unlink()

    duration_sec = len(full) / 24000
    manifest[scene_id] = True
    durations[scene_id] = round(duration_sec, 2)
    print(f"{scene_id}: {duration_sec:.2f}s")

(ROOT / "video" / "src").mkdir(parents=True, exist_ok=True)
(ROOT / "video" / "src" / "audioManifest.json").write_text(json.dumps(manifest, indent=2))
(ROOT / "audioDurations.json").write_text(json.dumps(durations, indent=2))
print("done")
