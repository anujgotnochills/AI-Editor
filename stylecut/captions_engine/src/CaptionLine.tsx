import React from "react";
import { getTemplate } from "./templates/registry";
import { CaptionLineProps } from "./types";

export const CaptionLine: React.FC<CaptionLineProps> = (props) => {
  const template = getTemplate(props.templateId);
  const Component = template.component;

  const mergedStyle = {
    ...template.defaultStyle,
    ...props.style,
  };

  return <Component {...props} style={mergedStyle} />;
};
