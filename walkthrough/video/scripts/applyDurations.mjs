import { readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const durations = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "audioDurations.json"), "utf-8")
);

const scenesPath = join(__dirname, "..", "src", "scenes.ts");
let src = readFileSync(scenesPath, "utf-8");

for (const [id, audioSec] of Object.entries(durations)) {
  const seconds = Math.ceil(audioSec + 1);
  const re = new RegExp(`(id: "${id}"[\\s\\S]*?seconds: )\\d+`, "m");
  if (!re.test(src)) {
    console.warn("no match for", id);
    continue;
  }
  src = src.replace(re, `$1${seconds}`);
  console.log(id, "->", seconds, "s (audio", audioSec, "s)");
}

writeFileSync(scenesPath, src);
