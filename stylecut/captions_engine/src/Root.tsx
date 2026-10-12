import React from "react";
import { Composition } from "remotion";
import { CaptionLine } from "./CaptionLine";
import { CaptionLineProps } from "./types";

export const Root: React.FC = () => {
  const defaultProps: CaptionLineProps = {
    templateId: "hook_gold",
    line: {
      id: "demo_1",
      text: "Being Ignored as a video editor",
      lead: "Being",
      keyword: "Ignored",
      tail: "as a video editor",
      start: 0,
      end: 2.5,
      duration: 2.5,
      words: [
        { word: "Being", start: 0, end: 0.5 },
        { word: "Ignored", start: 0.5, end: 1.5, highlight: true },
        { word: "as", start: 1.5, end: 1.7 },
        { word: "a", start: 1.7, end: 1.9 },
        { word: "video", start: 1.9, end: 2.2 },
        { word: "editor", start: 2.2, end: 2.5 },
      ],
    },
    canvas: {
      width: 1080,
      height: 1920,
      fps: 30,
    },
  };

  return (
    <>
      <Composition
        id="CaptionLine"
        component={CaptionLine}
        durationInFrames={75}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultProps}
        calculateMetadata={({ props }) => {
          const fps = props.canvas?.fps || 30;
          const durationSec = props.line?.duration || 2.5;
          const durationInFrames = Math.max(1, Math.round(durationSec * fps));
          return {
            fps,
            width: props.canvas?.width || 1080,
            height: props.canvas?.height || 1920,
            durationInFrames,
          };
        }}
      />
    </>
  );
};
