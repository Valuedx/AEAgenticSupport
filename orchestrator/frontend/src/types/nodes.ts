export type NodeCategory = "trigger" | "agent" | "action" | "logic";

export interface AgenticNodeData {
  [key: string]: unknown;
  label: string;
  nodeCategory: NodeCategory;
  description?: string;
  config: Record<string, unknown>;
  status?: "idle" | "running" | "completed" | "failed" | "suspended";
}

export interface PaletteItem {
  nodeCategory: NodeCategory;
  label: string;
  description: string;
  icon: string;
  defaultConfig: Record<string, unknown>;
}

import { REGISTRY_PALETTE } from "@/lib/registry";

export const NODE_PALETTE: PaletteItem[] = REGISTRY_PALETTE;
