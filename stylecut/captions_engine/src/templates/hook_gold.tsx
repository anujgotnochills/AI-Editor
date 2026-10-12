import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { CaptionLineProps } from "../types";

export const HookGoldTemplate: React.FC<CaptionLineProps> = ({ line, style }) => {
  const frame = useCurrentFrame();
  const { height, fps } = useVideoConfig();
  const scaleFactor = Math.max(0.5, height / 1920);

  const fontFamily = style?.fontFamily || "'Montserrat', 'Poppins', sans-serif";
  const textColor = style?.textColor || "#FFFFFF";
  const highlightColor = style?.highlightColor || "#FFD400";
  const shadowColor = style?.shadowColor || "rgba(0, 0, 0, 0.85)";
  const shadowBlur = (style?.shadowBlur ?? 8) * scaleFactor;

  // Spring entrance pop
  const pop = spring({
    frame,
    fps,
    config: {
      damping: 14,
      stiffness: 160,
      mass: 0.6,
    },
  });

  const scale = interpolate(pop, [0, 1], [0.88, 1]);
  const opacity = interpolate(pop, [0, 1], [0, 1]);

  // Derive lead, keyword, tail if not already split
  let lead = line.lead || "";
  let keyword = line.keyword || "";
  let tail = line.tail || "";

  if (!keyword && line.words && line.words.length > 0) {
    const highlighted = line.words.find((w) => w.highlight);
    if (highlighted) {
      keyword = highlighted.word;
    } else {
      const sorted = [...line.words].sort((a, b) => b.word.length - a.word.length);
      keyword = sorted[0]?.word || "";
    }

    const kwIdx = line.words.findIndex((w) => w.word === keyword);
    if (kwIdx !== -1) {
      lead = line.words.slice(0, kwIdx).map((w) => w.word).join(" ");
      tail = line.words.slice(kwIdx + 1).map((w) => w.word).join(" ");
    } else {
      lead = line.text;
    }
  } else if (!lead && !keyword && !tail) {
    keyword = line.text;
  }

  // Consistent vertical baseline anchor: 68% (standard lower third for reels/shorts)
  const yPercent = style?.yOffsetPercent ?? 68;

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        position: "relative",
        background: "transparent",
        pointerEvents: "none",
      }}
    >
      {/* 
        ANCHOR CONTAINER: Centered exactly at yPercent% (68%).
        Keyword is the physical anchor. Lead sits directly above it, 
        and Tail sits directly below it, so the keyword NEVER jumps vertically!
      */}
      <div
        style={{
          position: "absolute",
          top: `${yPercent}%`,
          left: "50%",
          transform: `translate(-50%, -50%) scale(${scale})`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          opacity,
          fontFamily,
          textShadow: `0px ${4 * scaleFactor}px ${shadowBlur}px ${shadowColor}, 0px ${2 * scaleFactor}px ${4 * scaleFactor}px rgba(0,0,0,0.9)`,
        }}
      >
        <div
          style={{
            position: "relative",
            display: "inline-flex",
            flexDirection: "column",
            alignItems: "center",
          }}
        >
          {/* Lead: anchored directly above keyword */}
          {lead ? (
            <div
              style={{
                position: "absolute",
                bottom: "100%",
                marginBottom: 8 * scaleFactor,
                whiteSpace: "nowrap",
                fontSize: (style?.fontSize ? style.fontSize * 0.55 : 54) * scaleFactor,
                fontWeight: 700,
                color: textColor,
                letterSpacing: "0.5px",
                lineHeight: 1.2,
              }}
            >
              {lead}
            </div>
          ) : null}

          {/* Keyword: THE FIXED VERTICAL ANCHOR */}
          {keyword ? (
            <div
              style={{
                fontSize: (style?.keywordFontSize ?? (style?.fontSize ? style.fontSize * 1.25 : 125)) * scaleFactor,
                fontWeight: 900,
                color: highlightColor,
                letterSpacing: "1px",
                lineHeight: 1.05,
                textTransform: "uppercase",
                filter: `drop-shadow(0 0 ${12 * scaleFactor}px ${highlightColor}44)`,
                whiteSpace: "nowrap",
              }}
            >
              {keyword}
            </div>
          ) : null}

          {/* Tail: anchored directly below keyword */}
          {tail ? (
            <div
              style={{
                position: "absolute",
                top: "100%",
                marginTop: 8 * scaleFactor,
                whiteSpace: "nowrap",
                fontSize: (style?.fontSize ? style.fontSize * 0.48 : 46) * scaleFactor,
                fontWeight: 600,
                color: textColor,
                letterSpacing: "0.5px",
                lineHeight: 1.2,
              }}
            >
              {tail}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};
