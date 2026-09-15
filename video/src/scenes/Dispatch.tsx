import React from 'react';
import {Caption, Eyebrow, Frame, Headline, TimelineRows} from '../components';

export const Dispatch: React.FC = () => {
  return (
    <Frame>
      <Eyebrow>05 · Dispatch</Eyebrow>
      <Headline size={74}>Rae makes the second call.</Headline>
      <TimelineRows
        rows={[
          {t: '00:00', who: 'rae', text: 'Rings I-95 Rapid Tow — 5.1 km away.'},
          {t: '00:40', who: 'rae', text: 'Busy — escalates to Harbor Point Recovery.'},
          {t: '01:10', who: 'ivy', text: 'Harbor Point Recovery is on the way — 25 minutes.'},
        ]}
      />
      <Caption>
        The moment the call ends, a risk score is calculated, a tow is dispatched, and the claim is already on the dispatcher{'’'}s board.
      </Caption>
    </Frame>
  );
};
