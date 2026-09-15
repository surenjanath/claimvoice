export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export interface SceneDef {
  key: string;
  durationInFrames: number;
  audio?: string; // public/ path, relative
  captionLead: number; // frames of silence before narration starts
}

// Each scene's duration is its narration clip's length (already includes a
// little trailing pad from the TTS render) plus a small lead-in, rounded up
// to a clean number so cuts land on a beat rather than mid-word.
export const SCENES: SceneDef[] = [
  {key: 'intro', durationInFrames: 105, captionLead: 0},
  {key: 'problem', durationInFrames: 301, audio: 'audio/line0.mp3', captionLead: 12},
  {key: 'solution', durationInFrames: 273, audio: 'audio/line1.mp3', captionLead: 10},
  {key: 'identity', durationInFrames: 249, audio: 'audio/line2.mp3', captionLead: 10},
  {key: 'guardrail', durationInFrames: 229, audio: 'audio/line3.mp3', captionLead: 10},
  {key: 'dispatch', durationInFrames: 266, audio: 'audio/line4.mp3', captionLead: 10},
  {key: 'handoff', durationInFrames: 204, audio: 'audio/line5.mp3', captionLead: 10},
  {key: 'close', durationInFrames: 244, audio: 'audio/line6.mp3', captionLead: 10},
  {key: 'outro', durationInFrames: 120, captionLead: 0},
];

export const TOTAL_FRAMES = SCENES.reduce((sum, s) => sum + s.durationInFrames, 0);

export const sceneStart = (key: string): number => {
  let acc = 0;
  for (const s of SCENES) {
    if (s.key === key) return acc;
    acc += s.durationInFrames;
  }
  throw new Error(`Unknown scene ${key}`);
};
