import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {Chip, fadeUp, Frame, Waveform} from '../components';
import {fonts, theme} from '../theme';

export const Intro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const title = fadeUp(frame, fps, 6, 30);
  const scale = interpolate(frame, [0, 20], [0.97, 1], {extrapolateRight: 'clamp'});

  return (
    <Frame>
      <div style={{...fadeUp(frame, fps, 0, 14)}}>
        <span
          style={{
            fontFamily: fonts.mono,
            fontSize: 22,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: theme.amber,
          }}
        >
          AssemblyAI Voice Agent Hackathon · lablab.ai
        </span>
      </div>
      <h1
        style={{
          ...title,
          transform: `${title.transform} scale(${scale})`,
          fontFamily: fonts.display,
          fontWeight: 800,
          fontSize: 190,
          color: theme.text,
          margin: '18px 0 0',
          lineHeight: 0.92,
        }}
      >
        ClaimVoice
      </h1>
      <div style={{marginTop: 20}}>
        <Waveform bars={30} />
      </div>
      <div style={{marginTop: 30, ...fadeUp(frame, fps, 24, 14)}}>
        <Chip tone="ok">CV-00018 · risk 71 · high · tow dispatched</Chip>
      </div>
    </Frame>
  );
};
