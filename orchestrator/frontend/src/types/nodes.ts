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

export const NODE_PALETTE: PaletteItem[] = [
  {
    nodeCategory: "trigger",
    label: "Webhook Trigger",
    description: "Start workflow on HTTP POST",
    icon: "webhook",
    defaultConfig: { method: "POST", path: "/webhook" },
  },
  {
    nodeCategory: "trigger",
    label: "Schedule Trigger",
    description: "Start workflow on a cron schedule",
    icon: "clock",
    defaultConfig: { cron: "0 * * * *" },
  },
  {
    nodeCategory: "agent",
    label: "LLM Agent",
    description: "AI reasoning node with prompt + model",
    icon: "brain",
    defaultConfig: {
      provider: "google",
      model: "gemini-2.5-flash",
      systemPrompt: "",
      temperature: 0.7,
      maxTokens: 4096,
    },
  },
  {
    nodeCategory: "agent",
    label: "ReAct Agent",
    description: "Iterative tool-calling agent loop",
    icon: "repeat",
    defaultConfig: {
      provider: "google",
      model: "gemini-2.5-flash",
      systemPrompt: "",
      maxIterations: 10,
      tools: [],
    },
  },
  {
    nodeCategory: "action",
    label: "MCP Tool",
    description: "Execute an MCP server tool",
    icon: "wrench",
    defaultConfig: { toolName: "", parameters: {} },
  },
  {
    nodeCategory: "action",
    label: "HTTP Request",
    description: "Make an external HTTP call",
    icon: "globe",
    defaultConfig: { url: "", method: "GET", headers: {}, body: "" },
  },
  {
    nodeCategory: "action",
    label: "Human Approval",
    description: "Pause and wait for human approval",
    icon: "user-check",
    defaultConfig: { approvalMessage: "", timeout: 3600 },
  },
  {
    nodeCategory: "logic",
    label: "Condition",
    description: "Branch based on a condition",
    icon: "git-branch",
    defaultConfig: { condition: "", trueLabel: "Yes", falseLabel: "No" },
  },
  {
    nodeCategory: "logic",
    label: "Merge",
    description: "Merge multiple branches",
    icon: "git-merge",
    defaultConfig: { strategy: "waitAll" },
  },
];
