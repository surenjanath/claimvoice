import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {Eyebrow, fadeUp, Frame, Headline} from '../components';
import {fonts, theme} from '../theme';

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <Frame>
      <Eyebrow>Talk to Ivy</Eyebrow>
      <Headline size={100}>Press Start. Say what happened.</Headline>
      <div
        style={{
          ...fadeUp(frame, fps, 24, 18),
          marginTop: 40,
          fontFamily: fonts.mono,
          fontSize: 30,
          color: theme.text,
          background: theme.ink,
          display: 'inline-block',
          padding: '16px 26px',
          borderRadius: 10,
          border: `1px solid ${theme.line}`,
        }}
      >
        github.com/surenjanath/claimvoice
      </div>
      <div style={{...fadeUp(frame, fps, 40, 12), marginTop: 30, fontFamily: fonts.body, fontSize: 20, color: theme.textDim}}>
        Built for the AssemblyAI Voice Agent Hackathon on lablab.ai, September 2026.
      </div>
    </Frame>
  );
};
