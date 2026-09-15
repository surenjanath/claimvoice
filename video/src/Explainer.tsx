import React from 'react';
import {Audio, Sequence, Series, staticFile} from 'remotion';
import {FontLoader} from './Fonts';
import {Intro} from './scenes/Intro';
import {Problem} from './scenes/Problem';
import {Solution} from './scenes/Solution';
import {Identity} from './scenes/Identity';
import {Guardrail} from './scenes/Guardrail';
import {Dispatch} from './scenes/Dispatch';
import {Handoff} from './scenes/Handoff';
import {Close} from './scenes/Close';
import {Outro} from './scenes/Outro';
import {SCENES} from './timeline';
import {theme} from './theme';

const COMPONENTS: Record<string, React.FC> = {
  intro: Intro,
  problem: Problem,
  solution: Solution,
  identity: Identity,
  guardrail: Guardrail,
  dispatch: Dispatch,
  handoff: Handoff,
  close: Close,
  outro: Outro,
};

export const Explainer: React.FC = () => {
  return (
    <div style={{width: '100%', height: '100%', background: theme.ink}}>
      <FontLoader />
      <Audio src={staticFile('audio/ambient_bed.mp3')} volume={0.9} />
      <Series>
        {SCENES.map((scene) => {
          const Component = COMPONENTS[scene.key];
          return (
            <Series.Sequence key={scene.key} durationInFrames={scene.durationInFrames}>
              <Component />
              {scene.audio ? (
                <Sequence from={scene.captionLead} durationInFrames={scene.durationInFrames - scene.captionLead}>
                  <Audio src={staticFile(scene.audio)} />
                </Sequence>
              ) : null}
            </Series.Sequence>
          );
        })}
      </Series>
    </div>
  );
};
