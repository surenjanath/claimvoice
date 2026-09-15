import React, {useEffect} from 'react';
import {continueRender, delayRender} from 'remotion';
import {GOOGLE_FONTS_HREF} from './theme';

let injected = false;

export const FontLoader: React.FC = () => {
  const [handle] = React.useState(() => delayRender('Loading fonts'));

  useEffect(() => {
    if (!injected) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = GOOGLE_FONTS_HREF;
      document.head.appendChild(link);
      injected = true;
    }
    // @ts-ignore - fonts is a standard document API not in this TS lib target
    const fontsReady: Promise<unknown> = document.fonts ? document.fonts.ready : Promise.resolve();
    fontsReady.then(() => continueRender(handle)).catch(() => continueRender(handle));
  }, [handle]);

  return null;
};
