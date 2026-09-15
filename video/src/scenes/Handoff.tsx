import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {Caption, Eyebrow, fadeUp, Frame, Headline} from '../components';
import {fonts, theme} from '../theme';

const CARDS = [
  {t: 'Empty rota', s: 'The caller is told in words; the fallback number is texted.'},
  {t: 'Lost status callback', s: 'watch_handoffs reaps anything stuck past its window.'},
  {t: 'One thing that never happens', s: 'Nothing calls the police on its own reading of a situation.'},
];

export const Handoff: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <Frame>
      <Eyebrow>06 · Handoff</Eyebrow>
      <Headline size={74}>When Ivy isn{'’'}t the right tool.</Headline>
      <div style={{display: 'flex', gap: 24, marginTop: 48}}>
        {CARDS.map((c, i) => {
          const st = fadeUp(frame, fps, 26 + i * 10, 18);
          return (
            <div
              key={c.t}
              style={{
                ...st,
                background: theme.paper2,
                border: `1px solid ${theme.line}`,
                borderRadius: 14,
                padding: '24px 26px',
                width: 380,
              }}
            >
              <div style={{fontFamily: fonts.body, fontWeight: 600, fontSize: 23, color: theme.text}}>{c.t}</div>
              <div style={{fontFamily: fonts.body, fontSize: 18, color: theme.textDim, marginTop: 10, lineHeight: 1.5}}>
                {c.s}
              </div>
            </div>
          );
        })}
      </div>
      <Caption>And when a caller needs a person instead of an agent, Ivy hands off. Never a silent dead end.</Caption>
    </Frame>
  );
};
