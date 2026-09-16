import { readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const script = JSON.parse(readFileSync(join(ROOT, "script.json"), "utf-8"));
const SCREENS = join(ROOT, "screenshots");

const B = script.brand;
// "mobile" -> narrow phone-frame media, side-by-side with copy.
// "web" -> wide browser-chrome-frame media, stacked above the copy.
const PLATFORM = script.platform === "web" ? "web" : "mobile";

// CUSTOMIZE: for each scene id in script.json, optionally provide a richer
// written kicker/body/bullets for the PDF (beyond the spoken narration).
// Any scene id left out just falls back to scene.narration as the body with
// no bullet list — so this can start as {} and be filled in per app.
const DETAILS = {
  "insight-schema": {
    kicker: "THE ONE API GOTCHA THAT BIT",
    body:
      "Every tool property here is something the model copies or picks — never composes. Found by bisecting the live tool schema field by field: add a property the model has to write in its own words, and the tool stops firing entirely. No error, no exception — the model just says “let me get that filed for you” and nothing happens.",
    bullets: [
      "description was cut from the schema; severity became an enum it picks from instead",
      "The readable summary line the dashboard shows is assembled server-side, from the facts the agent did send",
      "A test fails the build if a free-text field ever creeps back into the tool schema",
    ],
  },
  voice: {
    kicker: "TALK TO IVY",
    body:
      "Ivy asks nothing about a policy until she knows who's calling — the policy number and the last four digits of the phone on file, checked against the book before anything else. A wrong answer gets the same response whether the policy exists or not, because confirming a policy is real is itself a disclosure.",
    bullets: [
      "Universal-3 runs speech-to-text, the LLM, and text-to-speech over one websocket — no separate STT/LLM/TTS integration",
      "Drivability is asked, never inferred — a required boolean let the model guess from damage descriptions; an enum with “not asked” fixed it",
      "The mic stays in the browser and the API key never leaves the server — a 60-second session token is all the page ever holds",
    ],
  },
  dashboard: {
    kicker: "DISPATCHER BOARD",
    body:
      "The moment log_claim fires, a risk score, a drivability read, and a tow decision land on this board — scored by a readable rule set, not a black-box model, so every point has a stated reason.",
    bullets: [
      "Claims, risk factors, and dispatch status update live via a lightweight polling heartbeat, not a page refresh",
      "Filters for critical risk, lapsed cover, fraud watch, and unassigned claims triage the board at a glance",
      "Every row opens a full detail drawer with the transcript, tool calls, and dispatch history behind it",
    ],
  },
  conversations: {
    kicker: "EVERY CALL, KEPT",
    body:
      "The recording is a single stereo WAV — caller on the left channel, Ivy on the right — which makes a barge-in or a talk-over obvious instead of muddy. Click any line of the transcript and the audio jumps to that moment.",
    bullets: [
      "The two channels are aligned on the caller's own clock, since a burst reply would otherwise drift further ahead with every turn",
      "Spoken verification digits are redacted from both the transcript and the audio before they're ever stored",
      "A phone call has no browser to post a transcript, so its record is rebuilt from its own tool calls",
    ],
  },
  dispatches: {
    kicker: "GETTING A TRUCK MOVING",
    body:
      "Rae, a second published agent with a one-tool script, rings recovery operators ranked by distance from the claim's pin. If the first one is busy, she escalates to the next-closest — without redialling the truck that already said no.",
    bullets: [
      "A background watch chases a tow that's run past its promised time, then reassigns if it's still late on the next pass",
      "Vendor phone numbers are all in a range reserved for fiction — nothing dials a real number without an explicit opt-in",
      "The caller is told the truth at every step: who's coming, and how the story changed if the first operator couldn't take it",
    ],
  },
  insights: {
    kicker: "INSIGHTS",
    body:
      "This is the other half of the dashboard: not what happened, but how Ivy is doing. The tallest bar in “where Ivy needed a second attempt” is the question worth fixing in the prompt next.",
    bullets: [
      "A funnel from answered to verified to filed to dispatched shows exactly where callers drop off",
      "Every call log exports as JSONL, shaped for an eval set or a fine-tune rather than a spreadsheet",
      "Risk bands, incident mix, and identity pass rate, all direct-labelled so nothing depends on colour alone",
    ],
  },
  directory: {
    kicker: "DIRECTORY",
    body:
      "The policy book Ivy verifies every caller against. Pick anyone here, say their policy number and last four out loud, and the call proceeds exactly as if it were them calling in.",
    bullets: [
      "Every phone number in the demo book sits in a range reserved for fiction, and a test asserts it",
      "A held or lapsed policy is flagged as an instruction to Ivy, not a fact she'd read aloud to the caller",
      "Filter by liability-only cover, multiple vehicles, or prior claims to find a specific test scenario fast",
    ],
  },
  share: {
    kicker: "WHAT THE DRIVER SEES",
    body:
      "The reference number Ivy reads back is also a live link, texted to the caller. It's the same page whether they check it from the roadside or the morning after — no login, no app to install.",
    bullets: [
      "A photo upload lands on the dispatcher's board within seconds, tagged to the right claim automatically",
      "A short note from the caller — “I'm at the scene” — reaches the dispatcher the same way",
      "Addressed by an unguessable token, never a row ID, so nobody can page through someone else's claim by changing a number",
    ],
  },
  settings: {
    kicker: "AGENT CONFIGURATION",
    body:
      "Ivy's persona, prompt, voice, and turn-taking are all editable here and published straight to AssemblyAI — no redeploy, no code change, live on the very next call.",
    bullets: [
      "Presets change tone — empathetic, brief, multilingual-ready — while keeping the same tools and question order",
      "A full publish history means any prior version is one click from being restored into the draft",
      "The claim tool's schema itself lives in agent.json, read-only here, because it's the contract the backend depends on",
    ],
  },
};

// CUSTOMIZE: path to the target app's real logo file (PNG/SVG with a
// transparent or light background — it renders inside a white pill badge,
// see .mark CSS below). Copy the app's actual logo asset into
// walkthrough/assets/logo.png and reference it here, e.g.:
//   const LOGO_PATH = join(ROOT, "assets", "logo.png");
const LOGO_PATH = join(ROOT, "assets", "logo.png");
const fmtDate = new Date().toLocaleDateString("en-US", {
  year: "numeric",
  month: "long",
  day: "numeric",
});

// Scenes with image: null AND no code are pure title/divider cards meant
// for the video's pacing (a beat between real screens), not a documentable
// screen — the per-scene template has nothing to put in the media slot for
// them, so they rendered as an empty placeholder box. The PDF already has
// its own cover and closing pages carrying that same intro/outro
// narrative, so those two are redundant here. A scene.code entry (an
// insight — no screenshot, but real technical content) still gets a page,
// rendered as a console block instead of a screenshot frame.
const documentedScenes = script.scenes.filter((scene) => scene.image || scene.code);

const sectionsHtml = documentedScenes
  .map((scene, i) => {
    const d = DETAILS[scene.id] || {};
    const num = String(i + 1).padStart(2, "0");
    let media;
    if (scene.code) {
      const lines = scene.code
        .map((line) => `<div class="line${/NEVER FIRES/.test(line) ? " fail" : ""}">${line}</div>`)
        .join("");
      media = `<div class="code-frame">${lines}</div>`;
    } else {
      const imgPath = join(SCREENS, scene.image);
      media =
        PLATFORM === "web"
          ? `<div class="browser-frame"><div class="browser-bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>${scene.url ? `<span class="browser-url">${scene.url}</span>` : ""}</div><img src="file://${imgPath}" /></div>`
          : `<div class="phone-frame"><img src="file://${imgPath}" /></div>`;
    }
    return `
  <div class="page section-page">
    <section class="scene ${PLATFORM === "web" ? "web" : i % 2 === 1 ? "reverse" : ""}">
      <div class="scene-media">
        ${media}
      </div>
      <div class="scene-copy">
        <div class="scene-num">${num} / ${String(documentedScenes.length).padStart(2, "0")}</div>
        <div class="kicker">${d.kicker || scene.eyebrow}</div>
        <h2>${scene.title}</h2>
        <p class="lead">${d.body || scene.narration}</p>
        ${
          d.bullets
            ? `<ul>${d.bullets.map((b) => `<li>${b}</li>`).join("")}</ul>`
            : ""
        }
      </div>
    </section>
    <div class="footer-strip"><span>${script.appName}</span><span>${num} / ${String(documentedScenes.length).padStart(2, "0")} — ${scene.title}</span></div>
  </div>`;
  })
  .join("\n");

const tocHtml = documentedScenes
  .map((scene, i) => {
    const num = String(i + 1).padStart(2, "0");
    const d = DETAILS[scene.id] || {};
    return `<div class="toc-row"><span class="toc-num">${num}</span><span class="toc-title">${scene.title}</span><span class="toc-kicker">${d.kicker || scene.eyebrow}</span></div>`;
  })
  .join("\n");

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${script.appName} — App Walkthrough</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --primary: ${B.primary};
    --primary-light: ${B.primaryLight};
    --primary-dark: ${B.primaryDark};
    --gold: ${B.gold};
    --bg: ${B.bg};
    --surface: ${B.surface};
    --text: ${B.textPrimary};
    --text-secondary: ${B.textSecondary};
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: 'Manrope', sans-serif;
    color: var(--text);
    background: var(--bg);
    font-size: 12.5px;
  }
  h1, h2, h3 { font-family: 'Outfit', sans-serif; margin: 0; }
  @page { size: A4; margin: 0; }

  .page { width: 210mm; height: 297mm; position: relative; page-break-after: always; page-break-inside: avoid; overflow: hidden; }
  .page:last-child { page-break-after: auto; }

  /* Cover */
  .cover {
    background: radial-gradient(circle at 50% 15%, var(--primary-light) 0%, var(--primary) 55%, var(--primary-dark) 100%);
    color: #fff;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 40mm 20mm;
  }
  .mark {
    background: #fff; border-radius: 999px;
    padding: 7mm 14mm; margin-bottom: 14mm;
    box-shadow: 0 6px 18px -6px rgba(0,0,0,0.35);
    display: inline-flex;
  }
  .mark img { height: 12mm; display: block; }
  .mark.small { padding: 5mm 10mm; margin-bottom: 8mm; }
  .mark.small img { height: 8mm; }
  .cover .eyebrow {
    font-family: 'Manrope', sans-serif;
    font-weight: 700;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--gold);
    font-size: 12px;
    margin-bottom: 8mm;
  }
  .cover h1 {
    font-weight: 300;
    font-size: 46px;
    letter-spacing: -1px;
    line-height: 1.15;
    margin-bottom: 6mm;
  }
  .cover .tagline {
    font-size: 16px;
    color: rgba(255,255,255,0.85);
    font-weight: 500;
    margin-bottom: 26mm;
  }
  .cover .meta {
    font-size: 11px;
    color: rgba(255,255,255,0.6);
    letter-spacing: 1px;
  }
  .cover .divider { width: 48px; height: 2px; background: var(--gold); margin: 10mm 0; }

  /* TOC */
  .toc-page { padding: 26mm 22mm; background: var(--surface); }
  .toc-page .kicker-top {
    font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
    color: var(--primary); font-size: 11px; margin-bottom: 4mm;
  }
  .toc-page h1 { font-size: 30px; font-weight: 500; color: var(--text); margin-bottom: 14mm; }
  .toc-row {
    display: flex; align-items: baseline; gap: 6mm;
    padding: 4.2mm 0; border-bottom: 1px solid #E5E7EB;
  }
  .toc-num { font-family: 'Outfit'; font-weight: 600; color: var(--gold); font-size: 13px; width: 10mm; }
  .toc-title { font-family: 'Outfit'; font-weight: 500; font-size: 14px; flex: 1; color: var(--text); }
  .toc-kicker { font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 1px; }

  /* Section pages */
  .section-page { background: var(--surface); padding: 16mm 16mm; }
  .scene {
    display: flex;
    align-items: center;
    gap: 12mm;
    height: 265mm;
  }
  .scene.reverse { flex-direction: row-reverse; }
  .scene-media { flex: 0 0 68mm; display: flex; justify-content: center; }
  .phone-frame {
    width: 62mm;
    border-radius: 7mm;
    background: #0B0F0C;
    padding: 2.2mm;
    box-shadow: 0 10px 30px -8px rgba(18,32,24,0.4);
  }
  .phone-frame img { width: 100%; display: block; border-radius: 5.2mm; }
  .phone-frame.placeholder { height: 130mm; }

  /* Web platform: stacked layout, wide browser-chrome frame on top */
  .scene.web { flex-direction: column; align-items: stretch; gap: 8mm; height: auto; }
  .scene.web .scene-media { flex: none; width: 100%; }
  .browser-frame {
    width: 100%; max-height: 150mm; border-radius: 4mm; overflow: hidden;
    background: #E2E4EA; box-shadow: 0 10px 30px -8px rgba(18,32,24,0.35);
    display: flex; flex-direction: column;
  }
  /* object-fit: contain — a web screenshot's aspect ratio is unpredictable
     (a 16:9 viewport capture vs. a tall full-page capture), so this always
     letterboxes inside the capped height instead of blowing out the page. */
  .browser-frame img { width: 100%; flex: 1; min-height: 0; object-fit: contain; background: #fff; display: block; }
  .browser-frame.placeholder { height: 90mm; }
  .browser-bar { display: flex; align-items: center; gap: 3mm; padding: 2.6mm 4mm; background: #EDEEF2; }
  .browser-bar .dot { width: 2.6mm; height: 2.6mm; border-radius: 50%; display: inline-block; }

  /* An "insight" scene (scene.code, no screenshot) — a bisection or a
     before/after, same dark console treatment as the video's InsightCard. */
  .code-frame {
    width: 100%; border-radius: 4mm; background: #000000;
    border: 1px solid rgba(139,139,149,0.2); padding: 8mm 9mm;
    display: flex; flex-direction: column; gap: 3mm;
  }
  .code-frame .line { font-family: 'Courier New', monospace; font-size: 11.5px; color: #bfe8ff; white-space: pre; }
  .code-frame .line.fail { color: #ff6b5e; }
  .browser-bar .dot.r { background: #FF5F57; }
  .browser-bar .dot.y { background: #FEBC2E; }
  .browser-bar .dot.g { background: #28C840; }
  .browser-url {
    margin-left: 2mm; background: #fff; border-radius: 999px; padding: 1.3mm 4mm;
    font-size: 9px; color: var(--text-secondary); font-weight: 600;
  }

  .scene-copy { flex: 1; }
  .scene-num {
    font-family: 'Outfit'; font-weight: 600; color: var(--gold);
    font-size: 11px; letter-spacing: 1px; margin-bottom: 3mm;
  }
  .kicker {
    font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
    color: var(--primary); font-size: 10.5px; margin-bottom: 3mm;
  }
  .scene-copy h2 { font-size: 24px; font-weight: 500; color: var(--text); margin-bottom: 5mm; line-height: 1.2; }
  .scene-copy .lead { font-size: 12.5px; line-height: 1.65; color: var(--text); margin-bottom: 5mm; }
  .scene-copy ul { margin: 0; padding-left: 4.5mm; }
  .scene-copy li { font-size: 11.5px; line-height: 1.55; color: var(--text-secondary); margin-bottom: 2mm; }
  .scene-copy li::marker { color: var(--gold); }

  /* Closing */
  .closing {
    background: radial-gradient(circle at 50% 85%, var(--primary-light) 0%, var(--primary) 55%, var(--primary-dark) 100%);
    color: #fff; display: flex; flex-direction: column; align-items: center;
    justify-content: center; text-align: center; padding: 40mm 24mm;
  }
  .closing h2 { font-size: 30px; font-weight: 300; margin-bottom: 6mm; }
  .closing p { font-size: 13px; color: rgba(255,255,255,0.8); max-width: 130mm; line-height: 1.7; }
  .closing .tag { color: var(--gold); font-weight: 600; letter-spacing: 2px; text-transform: uppercase; font-size: 11px; margin-top: 10mm; }

  .footer-strip {
    position: absolute; bottom: 10mm; left: 16mm; right: 16mm;
    display: flex; justify-content: space-between;
    font-size: 9px; color: var(--text-secondary); letter-spacing: 0.5px;
  }
</style>
</head>
<body>

  <div class="page cover">
    <div class="mark"><img src="file://${LOGO_PATH}" /></div>
    <div class="eyebrow">Complete App Walkthrough</div>
    <h1>${script.appName}</h1>
    <div class="tagline">${script.tagline}</div>
    <div class="divider"></div>
    <div class="meta">Feature Guide &amp; Screen-by-Screen Reference · ${fmtDate}</div>
  </div>

  <div class="page toc-page">
    <div class="kicker-top">Contents</div>
    <h1>Everything in this guide</h1>
    ${tocHtml}
    <div class="footer-strip"><span>${script.appName}</span><span>Page 2</span></div>
  </div>

  ${sectionsHtml}

  <div class="page closing">
    <div class="mark small"><img src="file://${LOGO_PATH}" /></div>
    <h2>Thank you</h2>
    <p>This guide covered every screen in ${script.appName} — the call, the dispatcher board, the recording, dispatch, insights, the policy book, what the driver sees, and how the agent itself is configured.</p>
    <div class="tag">${script.tagline}</div>
  </div>

</body>
</html>`;

writeFileSync(join(ROOT, "docs", "walkthrough.html"), html);
console.log("wrote docs/walkthrough.html —", documentedScenes.length, "sections");
