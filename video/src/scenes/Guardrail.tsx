import React from 'react';
import {Caption, Eyebrow, Frame, Headline, TimelineRows} from '../components';

export const Guardrail: React.FC = () => {
  return (
    <Frame>
      <Eyebrow>04 · Guardrails</Eyebrow>
      <Headline size={74}>An enum, not a better prompt.</Headline>
      <TimelineRows
        rows={[
          {t: '64.6s', who: 'tool', text: 'log_claim({ is_drivable: "not_asked" })'},
          {t: '71.9s', who: 'ivy', text: 'Is the Honda Civic still driveable?'},
          {t: '78.5s', who: 'tool', text: 'log_claim({ is_drivable: "no" })'},
        ]}
      />
      <Caption>
        As the driver talks, Ivy fills in what happened — and when she doesn{'’'}t know something, she asks, instead of guessing.
      </Caption>
    </Frame>
  );
};
