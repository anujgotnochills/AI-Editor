import React from "react";
import { CaptionLineProps } from "../types";
import { HookGoldTemplate } from "./hook_gold";

export interface TemplateDefinition {
  id: string;
  name: string;
  description: string;
  component: React.FC<CaptionLineProps>;
  defaultStyle?: CaptionLineProps["style"];
}

export const TEMPLATE_REGISTRY: Record<string, TemplateDefinition> = {
  hook_gold: {
    id: "hook_gold",
    name: "Viral Hook (Golden Focus)",
    description: "3-tier viral hook with 120px golden emphasis keyword and subtle drop shadow pop",
    component: HookGoldTemplate,
    defaultStyle: {
      fontFamily: "'Montserrat', sans-serif",
      fontSize: 80,
      keywordFontSize: 120,
      textColor: "#FFFFFF",
      highlightColor: "#FFD400",
      shadowColor: "rgba(0, 0, 0, 0.85)",
      shadowBlur: 8,
      yOffsetPercent: 68,
    },
  },
};

export function getTemplate(templateId?: string): TemplateDefinition {
  if (templateId && TEMPLATE_REGISTRY[templateId]) {
    return TEMPLATE_REGISTRY[templateId];
  }
  return TEMPLATE_REGISTRY.hook_gold;
}

export function getAllTemplates(): TemplateDefinition[] {
  return Object.values(TEMPLATE_REGISTRY);
}
