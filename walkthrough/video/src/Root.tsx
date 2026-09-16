import React from "react";
import { Composition } from "remotion";
import { Walkthrough } from "./Walkthrough";
import { FPS, scenes, totalSeconds, VIDEO_WIDTH, VIDEO_HEIGHT } from "./scenes";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Walkthrough"
      component={Walkthrough}
      durationInFrames={Math.round(totalSeconds * FPS)}
      fps={FPS}
      width={VIDEO_WIDTH}
      height={VIDEO_HEIGHT}
      defaultProps={{ scenes }}
    />
  );
};
