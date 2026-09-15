import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {Caption, Eyebrow, fadeUp, Frame, Headline} from '../components';
import {fonts, theme} from '../theme';

const PAINS = ['Hold time', 'The same facts, twice', 'A second call for the tow'];

export const Problem: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <Frame>
      <Eyebrow>01 · The problem</Eyebrow>
      <Headline>The claim starts before anyone{'’'}s listening.</Headline>
      <div style={{display: 'flex', gap: 28, marginTop: 46}}>
        {PAINS.map((p, i) => {
          const st = fadeUp(frame, fps, 30 + i * 10, 18);
          return (
            <div
              key={p}
              style={{
                ...st,
                fontFamily: fonts.mono,
                fontSize: 24,
                color: theme.textDim,
                border: `1px solid ${theme.line}`,
                borderRadius: 10,
                padding: '16px 22px',
                background: theme.paper2,
              }}
            >
              {p}
            </div>
          );
        })}
      </div>
      <Caption>{'“'}When a driver crashes, the call center is the worst version of that day.{'”'}</Caption>
    </Frame>
  );
};
