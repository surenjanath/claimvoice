import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {Caption, Eyebrow, fadeUp, Frame, Headline} from '../components';
import {fonts, theme} from '../theme';

const NODES = [
  {t: 'Caller', s: 'browser or phone'},
  {t: 'Ivy', s: 'Universal-3 · HTTP tools'},
  {t: 'log_claim', s: 'risk score · tow decision'},
  {t: 'Dispatcher board', s: 'live, before hang-up'},
];

export const Solution: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <Frame>
      <Eyebrow>02 · How it works</Eyebrow>
      <Headline size={74}>One call. Four things happen before it ends.</Headline>
      <div style={{display: 'flex', alignItems: 'center', gap: 18, marginTop: 54}}>
        {NODES.map((n, i) => {
          const st = fadeUp(frame, fps, 26 + i * 10, 18);
          return (
            <React.Fragment key={n.t}>
              {i > 0 && (
                <div style={{...st, fontSize: 34, color: theme.amber}}>{'→'}</div>
              )}
              <div
                style={{
                  ...st,
                  background: theme.paper2,
                  border: `1px solid ${theme.line}`,
                  borderRadius: 12,
                  padding: '20px 24px',
                  minWidth: 220,
                }}
              >
                <div style={{fontFamily: fonts.body, fontWeight: 600, fontSize: 24, color: theme.text}}>{n.t}</div>
                <div style={{fontFamily: fonts.mono, fontSize: 17, color: theme.textDim, marginTop: 4}}>{n.s}</div>
              </div>
            </React.Fragment>
          );
        })}
      </div>
      <Caption>Built on AssemblyAI{'’'}s voice agent platform — speech to understanding to speech, in one live call.</Caption>
    </Frame>
  );
};
