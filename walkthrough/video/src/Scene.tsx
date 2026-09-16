import React from "react";
import { AbsoluteFill, Img, Audio, staticFile, useCurrentFrame, interpolate } from "remotion";
import type { Scene as SceneType } from "./scenes";
import { BRAND } from "./scenes";
import { OUTFIT, MANROPE } from "./fonts";

const FADE_FRAMES = 15;

// The only motion in these scenes: a crossfade at the start/end of each
// scene's Sequence, so cuts aren't jarring. No entrance springs, no
// pan/zoom, no sliding text — deliberately static otherwise.
function useFade(durationInFrames: number) {
  const frame = useCurrentFrame();
  return interpolate(
    frame,
    [0, FADE_FRAMES, durationInFrames - FADE_FRAMES, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
}

// The app's real wordmark is light text on transparent (it's drawn for the
// app's dark topbar), so it reads directly on these scenes' dark/brand
// backgrounds — no card behind it needed.
const LogoBadge: React.FC<{ width?: number; padding?: number }> = ({
  width = 220,
}) => <Img src={staticFile("logo.png")} style={{ width, display: "block" }} />;

// Generic browser-window chrome (traffic-light dots + a URL pill) around a
// screenshot, for "web" platform scenes. `object-fit: contain` inside so it
// doesn't matter whether the captured screenshot is a 16:9 viewport or a
// tall full-page capture — it always letterboxes cleanly instead of cropping.
const BrowserChrome: React.FC<{
  url?: string;
  width: number;
  height: number;
  children: React.ReactNode;
}> = ({ url, width, height, children }) => (
  <div
    style={{
      width,
      height,
      borderRadius: 20,
      backgroundColor: "#E2E4EA",
      boxShadow: "0 40px 80px -20px rgba(18,32,24,0.45)",
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
    }}
  >
    <div
      style={{
        height: 52,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "0 20px",
        backgroundColor: "#EDEEF2",
      }}
    >
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ width: 13, height: 13, borderRadius: 7, background: "#FF5F57" }} />
        <div style={{ width: 13, height: 13, borderRadius: 7, background: "#FEBC2E" }} />
        <div style={{ width: 13, height: 13, borderRadius: 7, background: "#28C840" }} />
      </div>
      {url ? (
        <div
          style={{
            flex: 1,
            maxWidth: 420,
            backgroundColor: "#fff",
            borderRadius: 999,
            padding: "7px 18px",
            fontFamily: MANROPE,
            fontWeight: 600,
            fontSize: 15,
            color: "#6B7280",
          }}
        >
          {url}
        </div>
      ) : null}
    </div>
    <div style={{ flex: 1, backgroundColor: "#fff", position: "relative" }}>{children}</div>
  </div>
);

const hasAudio = (id: string) => {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const manifest = require("./audioManifest.json");
    return Boolean(manifest[id]);
  } catch {
    return false;
  }
};

export const TitleCard: React.FC<{
  scene: SceneType;
  durationInFrames: number;
}> = ({ scene, durationInFrames }) => {
  const opacity = useFade(durationInFrames);
  const audioAvailable = hasAudio(scene.id);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BRAND.primary,
        backgroundImage: `radial-gradient(circle at 50% 20%, ${BRAND.primaryLight} 0%, ${BRAND.primary} 55%, ${BRAND.primaryDark} 100%)`,
        justifyContent: "center",
        alignItems: "center",
        opacity,
      }}
    >
      {audioAvailable ? <Audio src={staticFile(`audio/${scene.id}.mp3`)} /> : null}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 36 }}>
        <LogoBadge width={260} padding={26} />
        <div
          style={{
            fontFamily: OUTFIT,
            fontWeight: 300,
            fontSize: 72,
            letterSpacing: -1,
            color: "#FFFFFF",
            textAlign: "center",
            paddingLeft: 80,
            paddingRight: 80,
          }}
        >
          {scene.title}
        </div>
        <div
          style={{
            fontFamily: MANROPE,
            fontWeight: 600,
            fontSize: 28,
            color: BRAND.gold,
            letterSpacing: 3,
            textTransform: "uppercase",
            textAlign: "center",
          }}
        >
          {scene.eyebrow}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// A scene with no screenshot but real technical substance — a schema
// bisection, a race condition, a guardrail — rendered as a console block
// instead of a browser-chrome mockup. Distinct from TitleCard (which is a
// bare title/eyebrow divider): this one carries a body paragraph and a
// code block, so it reads as an engineering finding, not a section break.
export const InsightCard: React.FC<{
  scene: SceneType;
  durationInFrames: number;
}> = ({ scene, durationInFrames }) => {
  const opacity = useFade(durationInFrames);
  const audioAvailable = hasAudio(scene.id);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: BRAND.bg,
        opacity,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {audioAvailable ? <Audio src={staticFile(`audio/${scene.id}.mp3`)} /> : null}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `linear-gradient(${BRAND.textSecondary}22 1px, transparent 1px), linear-gradient(90deg, ${BRAND.textSecondary}22 1px, transparent 1px)`,
          backgroundSize: "64px 64px",
          opacity: 0.35,
        }}
      />
      <div
        style={{
          position: "relative",
          width: 1400,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            fontFamily: MANROPE,
            fontWeight: 700,
            fontSize: 22,
            letterSpacing: 3,
            color: BRAND.gold,
            textTransform: "uppercase",
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}
        >
          <span style={{ width: 36, height: 2, background: BRAND.gold, display: "inline-block" }} />
          {scene.eyebrow}
        </div>
        <div
          style={{
            marginTop: 22,
            fontFamily: OUTFIT,
            fontWeight: 500,
            fontSize: 52,
            lineHeight: 1.1,
            color: BRAND.textPrimary,
            maxWidth: 1200,
          }}
        >
          {scene.title}
        </div>
        <div
          style={{
            marginTop: 24,
            fontFamily: MANROPE,
            fontWeight: 400,
            fontSize: 24,
            lineHeight: 1.55,
            color: BRAND.textSecondary,
            maxWidth: 1080,
          }}
        >
          {scene.narration}
        </div>
        {scene.code ? (
          <div
            style={{
              marginTop: 40,
              backgroundColor: "#000000",
              border: `1px solid ${BRAND.textSecondary}33`,
              borderRadius: 16,
              padding: "32px 36px",
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            {scene.code.map((line, i) => {
              const isFail = /NEVER FIRES/.test(line);
              return (
                <div
                  key={i}
                  style={{
                    fontFamily: "monospace",
                    fontSize: 22,
                    color: isFail ? "#ff6b5e" : "#bfe8ff",
                    whiteSpace: "pre",
                  }}
                >
                  {line}
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

export const ScreenShowcase: React.FC<{
  scene: SceneType;
  durationInFrames: number;
  index: number;
}> = ({ scene, durationInFrames }) => {
  const opacity = useFade(durationInFrames);
  const audioAvailable = hasAudio(scene.id);

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, opacity }}>
      {audioAvailable ? <Audio src={staticFile(`audio/${scene.id}.mp3`)} /> : null}

      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 260,
          background: `linear-gradient(135deg, ${BRAND.primary} 0%, ${BRAND.primaryLight} 100%)`,
        }}
      />

      <AbsoluteFill
        style={{ justifyContent: "flex-start", alignItems: "center", paddingTop: 56 }}
      >
        <LogoBadge width={128} padding={12} />

        <div
          style={{
            marginTop: 20,
            fontFamily: MANROPE,
            fontWeight: 700,
            fontSize: 18,
            letterSpacing: 3,
            color: BRAND.gold,
            textTransform: "uppercase",
          }}
        >
          {scene.eyebrow}
        </div>

        <div
          style={{
            marginTop: 14,
            fontFamily: OUTFIT,
            fontWeight: 500,
            fontSize: 44,
            color: "#FFFFFF",
            textAlign: "center",
            paddingLeft: 70,
            paddingRight: 70,
          }}
        >
          {scene.title}
        </div>

        <div
          style={{
            marginTop: 36,
            width: 620,
            height: 1330,
            borderRadius: 56,
            backgroundColor: "#0B0F0C",
            padding: 14,
            boxShadow: "0 40px 80px -20px rgba(18,32,24,0.55)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: "100%",
              height: "100%",
              borderRadius: 44,
              overflow: "hidden",
              backgroundColor: "#fff",
              position: "relative",
            }}
          >
            {scene.image ? (
              <Img
                src={staticFile(`screens/${scene.image}`)}
                style={{ width: "100%", display: "block" }}
              />
            ) : null}
          </div>
        </div>
      </AbsoluteFill>

      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 170,
          background: `linear-gradient(0deg, ${BRAND.bg} 40%, rgba(248,249,250,0))`,
        }}
      />
    </AbsoluteFill>
  );
};

// Landscape layout for "web" platform (websites / web apps): a dark side
// panel with the title copy, and a browser-chrome-framed screenshot centered
// in the rest of the 1920x1080 frame. No pan/zoom, matching the mobile layout.
export const WebScreenShowcase: React.FC<{
  scene: SceneType;
  durationInFrames: number;
  index: number;
}> = ({ scene, durationInFrames }) => {
  const opacity = useFade(durationInFrames);
  const audioAvailable = hasAudio(scene.id);
  const SIDE_PANEL_WIDTH = 620;

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, opacity, flexDirection: "row" }}>
      {audioAvailable ? <Audio src={staticFile(`audio/${scene.id}.mp3`)} /> : null}

      <div
        style={{
          width: SIDE_PANEL_WIDTH,
          flexShrink: 0,
          height: "100%",
          background: `linear-gradient(160deg, ${BRAND.primary} 0%, ${BRAND.primaryDark} 100%)`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "0 64px",
        }}
      >
        <LogoBadge width={150} padding={14} />
        <div
          style={{
            marginTop: 32,
            fontFamily: MANROPE,
            fontWeight: 700,
            fontSize: 17,
            letterSpacing: 3,
            color: BRAND.gold,
            textTransform: "uppercase",
          }}
        >
          {scene.eyebrow}
        </div>
        <div
          style={{
            marginTop: 14,
            fontFamily: OUTFIT,
            fontWeight: 500,
            fontSize: 46,
            lineHeight: 1.15,
            color: "#FFFFFF",
          }}
        >
          {scene.title}
        </div>
      </div>

      {/* Plain positioned div, not AbsoluteFill: AbsoluteFill hardcodes
          width: 100%, so overriding just `left` on it pushes this region
          off the right edge of the frame instead of shrinking it. */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: SIDE_PANEL_WIDTH,
          right: 0,
          bottom: 0,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          padding: 70,
        }}
      >
        <BrowserChrome url={scene.url} width={1140} height={780}>
          {scene.image ? (
            <Img
              src={staticFile(`screens/${scene.image}`)}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "contain",
                display: "block",
              }}
            />
          ) : null}
        </BrowserChrome>
      </div>
    </AbsoluteFill>
  );
};
