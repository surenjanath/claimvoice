import React from 'react';
import {Caption, ConsoleBlock, Eyebrow, Frame, Headline} from '../components';

export const Identity: React.FC = () => {
  return (
    <Frame>
      <Eyebrow>03 · Verification</Eyebrow>
      <Headline size={74}>She won{'’'}t say a name until she{'’'}s sure.</Headline>
      <ConsoleBlock
        lines={[
          {k: '"verified"', v: 'false'},
          {k: '"attempts_remaining"', v: '2'},
          {k: '"message"', v: '"That does not match what I have on file."'},
          {raw: '// spoken to the caller. instructions steer Ivy — never heard.'},
        ]}
      />
      <Caption>
        A policy number, the last four digits, checked before anything else.
      </Caption>
    </Frame>
  );
};
