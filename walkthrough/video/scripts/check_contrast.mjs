// A pre-render gate against exactly the bug class that shipped once already:
// LogoBadge's white pill swallowing the (then light-text) wordmark. That was
// an image, not text a linter can see — but the same mistake in CSS (a text
// color that quietly stops reading against its background) is checkable, so
// every real text/background pair Scene.tsx draws is listed and scored here
// against WCAG 2.1 thresholds (4.5:1 normal text, 3:1 large text: >=24px, or
// >=18.66px bold). Run after editing Scene.tsx and before re-rendering:
//
//   node scripts/check_contrast.mjs
//
// Gradient backgrounds are reduced to the single color the text actually
// sits over (its approximate position along the gradient's axis/stops, not
// the gradient's most extreme stop — checking against a stop the text never
// reaches produces false failures, which is worse than no gate at all
// because it trains you to ignore FAIL output). Each gradient-derived `bg`
// below documents which stops and position it was derived from.
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..");
const { brand } = JSON.parse(readFileSync(join(ROOT, "script.json"), "utf-8"));

const hexToRgb = (hex) => {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

const mix = (hexA, hexB, t) => {
  const [ar, ag, ab] = hexToRgb(hexA);
  const [br, bg, bb] = hexToRgb(hexB);
  const toHex = (v) => Math.round(v).toString(16).padStart(2, "0");
  return `#${toHex(ar + (br - ar) * t)}${toHex(ag + (bg - ag) * t)}${toHex(ab + (bb - ab) * t)}`;
};

// Alpha-composite a foreground color (with alpha) over an opaque rgb background.
const compositeRgb = (hex, alpha, bgRgb) => {
  const [fr, fg, fb] = hexToRgb(hex);
  const [br, bg, bb] = bgRgb;
  return [fr * alpha + br * (1 - alpha), fg * alpha + bg * (1 - alpha), fb * alpha + bb * (1 - alpha)];
};

const relLuminance = ([r, g, b]) => {
  const chan = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
};

const contrastRatio = (rgbA, rgbB) => {
  const lA = relLuminance(rgbA);
  const lB = relLuminance(rgbB);
  const [lighter, darker] = lA > lB ? [lA, lB] : [lB, lA];
  return (lighter + 0.05) / (darker + 0.05);
};

// Representative background colors, derived once from each gradient's stops
// and the approximate position the text sits at within it (see comments at
// each use below). Recompute these if the gradients in Scene.tsx change.
const G = {
  // TitleCard: radial-gradient(circle at 50% 20%, primaryLight 0%, primary 55%,
  // primaryDark 100%). Content is centered (50% 50%), ~25% of the way from
  // the gradient's center to its farthest corner -> ~46% of the way from
  // primaryLight to primary. Approximated as their midpoint.
  titleCardMid: mix(brand.primaryLight, brand.primary, 0.5),
  // ScreenShowcase: linear-gradient(135deg, primary 0%, primaryLight 100%)
  // over a wide (1920px), short (260px) banner — horizontal position (center)
  // dominates the 135deg axis far more than vertical, so center ~= midpoint.
  screenShowcaseMid: mix(brand.primary, brand.primaryLight, 0.5),
  // WebScreenShowcase: linear-gradient(160deg, primary 0%, primaryDark 100%)
  // over the full-height side panel, text vertically centered -> ~midpoint.
  webShowcaseMid: mix(brand.primary, brand.primaryDark, 0.5),
};

// Every real text/background pair Scene.tsx draws. `scrim` (0-1 alpha of
// black) matches the rgba(0,0,0,x) backing now behind eyebrow chips.
const PAIRS = [
  // TitleCard
  { label: "TitleCard title (#FFFFFF, 72px, no scrim)", fg: "#FFFFFF", bg: G.titleCardMid, large: true },
  { label: "TitleCard eyebrow (gold, 28px 600, scrim .4)", fg: brand.gold, bg: G.titleCardMid, scrim: 0.4, large: true },

  // InsightCard — flat BRAND.bg behind everything, no scrim needed
  { label: "InsightCard eyebrow (gold, 22px 700)", fg: brand.gold, bg: brand.bg, large: true },
  { label: "InsightCard title (textPrimary, 52px 500)", fg: brand.textPrimary, bg: brand.bg, large: true },
  { label: "InsightCard narration (textSecondary, 24px)", fg: brand.textSecondary, bg: brand.bg, large: false },
  { label: "InsightCard code line, ok (#bfe8ff on #000)", fg: "#bfe8ff", bg: "#000000", large: false },
  { label: "InsightCard code line, fail (#ff6b5e on #000)", fg: "#ff6b5e", bg: "#000000", large: false },

  // ScreenShowcase (mobile-platform variant; not used while script.json's
  // platform is "web", kept checked in case that ever switches)
  { label: "ScreenShowcase eyebrow (gold, 18px 700, scrim .4)", fg: brand.gold, bg: G.screenShowcaseMid, scrim: 0.4, large: false },
  { label: "ScreenShowcase title (#FFFFFF, 44px 500, no scrim)", fg: "#FFFFFF", bg: G.screenShowcaseMid, large: true },

  // WebScreenShowcase (the platform actually rendered — every showcase scene)
  { label: "WebScreenShowcase eyebrow (gold, 17px 700, scrim .4)", fg: brand.gold, bg: G.webShowcaseMid, scrim: 0.4, large: false },
  { label: "WebScreenShowcase title (#FFFFFF, 46px 500, no scrim)", fg: "#FFFFFF", bg: G.webShowcaseMid, large: true },

  // BrowserChrome address pill
  { label: "BrowserChrome url (#6B7280 on #fff)", fg: "#6B7280", bg: "#FFFFFF", large: false },
];

let failed = false;
console.log("Contrast check (WCAG 2.1) — script.json brand tokens\n");

for (const { label, fg, bg, scrim, large } of PAIRS) {
  const bgRgb = scrim ? compositeRgb("#000000", scrim, hexToRgb(bg)) : hexToRgb(bg);
  const threshold = large ? 3.0 : 4.5;
  const ratio = contrastRatio(hexToRgb(fg), bgRgb);
  const ok = ratio >= threshold;
  if (!ok) failed = true;
  const mark = ok ? "PASS" : "FAIL";
  console.log(`  ${mark}  ${label.padEnd(52)} ${ratio.toFixed(2)}:1  (needs ${threshold}:1)`);
}

console.log("");
if (failed) {
  console.error("Contrast check failed — fix the FAIL rows above before rendering.");
  process.exit(1);
} else {
  console.log("All text/background pairs clear their WCAG threshold.");
}
