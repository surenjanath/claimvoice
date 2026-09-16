// ClaimVoice's own design system (claims/static/claims/css/app.css) names one
// sans stack for everything — "-apple-system, ..., Inter, system-ui" — no
// separate display face. Inter for both exports matches that; weight is set
// per-usage in Scene.tsx, same as the app's own CSS does.
import { loadFont as loadHeadingFont } from "@remotion/google-fonts/Inter";
import { loadFont as loadBodyFont } from "@remotion/google-fonts/Inter";

// Only the weights Scene.tsx actually sets (300/500/600/700) — the default
// loads every weight/subset combination, which is both slow and noisy.
const WEIGHTS = ["300", "500", "600", "700"] as const;
const heading = loadHeadingFont("normal", { weights: WEIGHTS, subsets: ["latin"] });
const body = loadBodyFont("normal", { weights: WEIGHTS, subsets: ["latin"] });

export const OUTFIT = heading.fontFamily;
export const MANROPE = body.fontFamily;
