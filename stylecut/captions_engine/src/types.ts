export interface WordTiming {
  word: string;
  start: number; // in seconds
  end: number;   // in seconds
  highlight?: boolean;
}

export interface LineData {
  id: string;
  text: string;
  start: number;      // timeline start time in seconds
  end: number;        // timeline end time in seconds
  duration: number;   // duration in seconds
  words: WordTiming[];
  lead?: string;
  keyword?: string;
  tail?: string;
  emphasis?: string;
  emoji?: string;
}

export interface CanvasConfig {
  width: number;
  height: number;
  fps: number;
}

export interface StyleOverrides {
  fontFamily?: string;
  fontSize?: number;
  keywordFontSize?: number;
  textColor?: string;
  highlightColor?: string;
  shadowColor?: string;
  shadowBlur?: number;
  textTransform?: "uppercase" | "lowercase" | "capitalize" | "none";
  position?: "top" | "center" | "bottom";
  yOffsetPercent?: number; // e.g. 70 for lower third
}

export interface CaptionLineProps {
  templateId?: string;
  line: LineData;
  style?: StyleOverrides;
  canvas?: CanvasConfig;
}

export interface CaptionsSpec {
  templateId: string;
  canvas: CanvasConfig;
  styleOverrides?: StyleOverrides;
  maxWordsPerLine?: number;
  wordsPerLine?: number;
  lines: LineData[];
}
