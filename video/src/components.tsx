import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {fonts, theme} from './theme';

export const fadeUp = (frame: number, fps: number, delay = 0, distance = 26) => {
  const s = spring({frame: frame - delay, fps, config: {damping: 200, mass: 0.6}});
  return {
    opacity: interpolate(s, [0, 1], [0, 1]),
    transform: `translateY(${interpolate(s, [0, 1], [distance, 0])}px)`,
  };
};

export const Eyebrow: React.FC<{children: React.ReactNode; delay?: number}> = ({children, delay = 0}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div
      style={{
        ...fadeUp(frame, fps, delay),
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        fontFamily: fonts.mono,
        fontSize: 22,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        color: theme.amber,
        marginBottom: 28,
      }}
    >
      <span style={{width: 32, height: 2, background: theme.amber, display: 'inline-block'}} />
      {children}
    </div>
  );
};

export const Headline: React.FC<{children: React.ReactNode; delay?: number; size?: number}> = ({
  children,
  delay = 4,
  size = 84,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <h1
      style={{
        ...fadeUp(frame, fps, delay, 34),
        fontFamily: fonts.display,
        fontWeight: 800,
        fontSize: size,
        lineHeight: 0.98,
        color: theme.text,
        margin: 0,
        maxWidth: 1500,
      }}
    >
      {children}
    </h1>
  );
};

export const Lede: React.FC<{children: React.ReactNode; delay?: number}> = ({children, delay = 14}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <p
      style={{
        ...fadeUp(frame, fps, delay, 20),
        fontFamily: fonts.body,
        fontSize: 32,
        lineHeight: 1.5,
        color: theme.textDim,
        maxWidth: 1180,
        marginTop: 26,
      }}
    >
      {children}
    </p>
  );
};

export const Chip: React.FC<{children: React.ReactNode; tone?: 'ok' | 'amber' | 'signal'}> = ({
  children,
  tone = 'ok',
}) => {
  const color = tone === 'ok' ? theme.ok : tone === 'amber' ? theme.amber : theme.signal;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 10,
        fontFamily: fonts.mono,
        fontSize: 24,
        padding: '12px 20px',
        borderRadius: 10,
        background: theme.paper2,
        border: `1px solid ${theme.line}`,
        color: theme.text,
      }}
    >
      <span style={{width: 10, height: 10, borderRadius: 999, background: color, flex: 'none'}} />
      {children}
    </span>
  );
};

export const Waveform: React.FC<{bars?: number; color?: string; height?: number}> = ({
  bars = 26,
  color = theme.amber,
  height = 90,
}) => {
  const frame = useCurrentFrame();
  const seeds = React.useMemo(
    () => Array.from({length: bars}, (_, i) => (Math.sin(i * 12.9898) * 43758.5453) % 1),
    [bars]
  );
  return (
    <div style={{display: 'flex', alignItems: 'flex-end', gap: 6, height}}>
      {seeds.map((seed, i) => {
        const phase = Math.abs(seed) * Math.PI * 2;
        const scale = 0.22 + 0.78 * Math.abs(Math.sin(frame / 9 + phase));
        return (
          <div
            key={i}
            style={{
              width: 8,
              height: `${scale * 100}%`,
              borderRadius: 4,
              background: `linear-gradient(180deg, ${theme.amberGlow}, ${color})`,
            }}
          />
        );
      })}
    </div>
  );
};

export const ConsoleBlock: React.FC<{lines: {k?: string; v?: string; c?: string; raw?: string}[]; delay?: number}> = ({
  lines,
  delay = 20,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div
      style={{
        ...fadeUp(frame, fps, delay, 16),
        marginTop: 44,
        background: theme.ink,
        border: `1px solid ${theme.line}`,
        borderRadius: 16,
        padding: '32px 38px',
        fontFamily: fonts.mono,
        fontSize: 26,
        lineHeight: 1.75,
        color: '#bfe8ff',
        maxWidth: 1200,
      }}
    >
      {lines.map((l, i) =>
        l.raw !== undefined ? (
          <div key={i} style={{color: theme.textDim}}>
            {l.raw}
          </div>
        ) : (
          <div key={i}>
            <span style={{color: '#8ab4ff'}}>{l.k}</span>
            <span style={{color: theme.textDim}}>: </span>
            <span style={{color: '#ffd479'}}>{l.v}</span>
            {l.c ? <span style={{color: theme.textDim}}> // {l.c}</span> : null}
          </div>
        )
      )}
    </div>
  );
};

export const TimelineRows: React.FC<{
  rows: {t: string; who: string; text: string}[];
  delay?: number;
}> = ({rows, delay = 18}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div style={{marginTop: 44, display: 'flex', flexDirection: 'column', maxWidth: 1300}}>
      {rows.map((r, i) => {
        const st = fadeUp(frame, fps, delay + i * 10, 14);
        return (
          <div
            key={i}
            style={{
              ...st,
              display: 'grid',
              gridTemplateColumns: '150px 130px 1fr',
              gap: 24,
              padding: '20px 0',
              borderTop: i === 0 ? 'none' : `1px solid ${theme.line}`,
            }}
          >
            <div style={{fontFamily: fonts.mono, fontSize: 24, color: theme.textDim}}>{r.t}</div>
            <div
              style={{
                fontFamily: fonts.mono,
                fontSize: 20,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: theme.signal,
              }}
            >
              {r.who}
            </div>
            <div style={{fontFamily: fonts.body, fontSize: 28, color: theme.text}}>{r.text}</div>
          </div>
        );
      })}
    </div>
  );
};

export const StatTile: React.FC<{n: string; label: string; delay?: number}> = ({n, label, delay = 0}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div
      style={{
        ...fadeUp(frame, fps, delay, 20),
        background: theme.paper2,
        border: `1px solid ${theme.line}`,
        borderRadius: 16,
        padding: '30px 34px',
        minWidth: 260,
      }}
    >
      <div style={{fontFamily: fonts.display, fontWeight: 800, fontSize: 64, color: theme.amber}}>{n}</div>
      <div style={{fontFamily: fonts.body, fontSize: 24, color: theme.textDim, marginTop: 6}}>{label}</div>
    </div>
  );
};

export const Caption: React.FC<{children: React.ReactNode; delay?: number}> = ({children, delay = 8}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div
      style={{
        ...fadeUp(frame, fps, delay, 16),
        marginTop: 64,
        fontFamily: fonts.body,
        fontWeight: 500,
        fontSize: 36,
        lineHeight: 1.4,
        color: theme.text,
        maxWidth: 1400,
        borderLeft: `4px solid ${theme.amber}`,
        paddingLeft: 26,
      }}
    >
      {children}
    </div>
  );
};

export const Frame: React.FC<{children: React.ReactNode}> = ({children}) => (
  <div
    style={{
      width: '100%',
      height: '100%',
      background: `radial-gradient(1200px 700px at 18% 10%, ${theme.paper3} 0%, ${theme.ink} 62%)`,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      padding: '0 150px',
      position: 'relative',
      overflow: 'hidden',
    }}
  >
    <div
      style={{
        position: 'absolute',
        inset: 0,
        backgroundImage: `linear-gradient(${theme.line}22 1px, transparent 1px), linear-gradient(90deg, ${theme.line}22 1px, transparent 1px)`,
        backgroundSize: '64px 64px',
        opacity: 0.35,
        pointerEvents: 'none',
      }}
    />
    <div style={{position: 'relative', zIndex: 1}}>{children}</div>
  </div>
);
