import React from 'react';
import {Caption, Eyebrow, Frame, Headline, StatTile} from '../components';

export const Close: React.FC = () => {
  return (
    <Frame>
      <Eyebrow>07 · Under the hood</Eyebrow>
      <Headline size={74}>Built to be checked, not just demoed.</Headline>
      <div style={{display: 'flex', gap: 24, marginTop: 50}}>
        <StatTile n="201" label="automated tests" delay={22} />
        <StatTile n="$0" label="to deploy" delay={32} />
        <StatTile n="2" label="agents, one platform" delay={42} />
      </div>
      <Caption>
        Two hundred and one tests. Free to deploy. Built end to end on AssemblyAI.
      </Caption>
    </Frame>
  );
};
