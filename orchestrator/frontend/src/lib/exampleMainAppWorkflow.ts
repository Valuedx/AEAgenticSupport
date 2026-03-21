import type { Edge, Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";

/**
 * Parity sketch for **AutomationEdge AI Studio** (`main.py` → `MessageGateway` → `AgentRouter`).
 *
 * Main app behavior (see repo root `gateway/message_gateway.py`, `agents/agent_router.py`):
 * - Messages enter the gateway; optional multi-agent **router** scores specialists.
 * - **diagnostic_agent** — logs, status, investigation (`allowed_categories` status/logs/…).
 * - **remediation_agent** — restart, fix, execute (`remediation`, `notification`, `config`).
 * - **rca_agent** — RCA / postmortem reports (`generate_rca_report`-style synthesis).
 * - **ops_orchestrator** — default catch-all for RPA/workflow/automation (full orchestrator loop).
 * - Risky remediation aligns with **Human Approval** (gateway uses `AWAITING_APPROVAL` on the monolithic orchestrator).
 *
 * This DAG encodes the same **routing intent** with an LLM Router + chained Conditions.
 * Connect your MCP tools in ReAct nodes to approximate specialist tool categories.
 *
 * Trigger JSON (execute):
 * {
 *   "session_id": "ae-session-001",
 *   "message": "Workflow 2887 failed — pull the error logs and suggest a fix",
 *   "user_role": "technical",
 *   "user_id": "user@company.com"
 * }
 */
export const EXAMPLE_AUTOMATIONEDGE_MAIN_WORKFLOW: { nodes: Node[]; edges: Edge[] } = {
  nodes: [
    {
      id: "node_1",
      type: "agenticNode",
      position: { x: 0, y: 260 },
      data: {
        label: "Webhook Trigger",
        nodeCategory: "trigger",
        config: {
          icon: "webhook",
          method: "POST",
          path: "/ae/ops/inbound",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_2",
      type: "agenticNode",
      position: { x: 220, y: 260 },
      data: {
        label: "Load Conversation State",
        nodeCategory: "action",
        config: {
          icon: "history",
          sessionIdExpression: "trigger.session_id",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_3",
      type: "agenticNode",
      position: { x: 460, y: 260 },
      data: {
        label: "LLM Router",
        nodeCategory: "agent",
        config: {
          icon: "route",
          provider: "google",
          model: "gemini-2.5-flash",
          // First = fallback when the model returns an unknown label (matches ops_orchestrator as default catch-all).
          intents: ["ops_orchestrator", "diagnostics", "remediation", "rca_report"],
          historyNodeId: "node_2",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_4",
      type: "agenticNode",
      position: { x: 700, y: 260 },
      data: {
        label: "Condition",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'node_3.intent == "diagnostics"',
          trueLabel: "Diagnostics",
          falseLabel: "Next",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_5",
      type: "agenticNode",
      position: { x: 940, y: 360 },
      data: {
        label: "Condition",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'node_3.intent == "remediation"',
          trueLabel: "Remediation",
          falseLabel: "Next",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_6",
      type: "agenticNode",
      position: { x: 1180, y: 460 },
      data: {
        label: "Condition",
        nodeCategory: "logic",
        config: {
          icon: "git-branch",
          condition: 'node_3.intent == "rca_report"',
          trueLabel: "RCA",
          falseLabel: "Ops default",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_7",
      type: "agenticNode",
      position: { x: 980, y: 80 },
      data: {
        label: "ReAct Agent",
        nodeCategory: "agent",
        config: {
          icon: "repeat",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You are the Diagnostic Specialist (parity: agents/diagnostic_agent.py). " +
            "Investigate RPA/workflow failures: pull status, logs, dependencies, files. " +
            "Prefer MCP tools for status, logs, and diagnostics. Summarize evidence before suggesting fixes.",
          maxIterations: 12,
          tools: [],
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_8",
      type: "agenticNode",
      position: { x: 1180, y: 220 },
      data: {
        label: "ReAct Agent",
        nodeCategory: "agent",
        config: {
          icon: "repeat",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You are the Remediation Specialist (parity: agents/remediation_agent.py). " +
            "Execute corrective actions: restart workflows, notifications, safe config changes. " +
            "Use MCP remediation/notification tools when available. Confirm impact before destructive steps.",
          maxIterations: 12,
          tools: [],
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_9",
      type: "agenticNode",
      position: { x: 1420, y: 400 },
      data: {
        label: "LLM Agent",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You are the RCA Specialist (parity: agents/rca_agent.py / generate_rca_report). " +
            "Produce a structured incident report: Summary, Timeline, Root cause, Impact, Prevention, " +
            "Audience (business vs technical) using trigger.user_role when present. " +
            "Use conversation and prior node outputs in context; if data is thin, say what is missing.",
          temperature: 0.35,
          maxTokens: 4096,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_10",
      type: "agenticNode",
      position: { x: 1420, y: 560 },
      data: {
        label: "LLM Agent",
        nodeCategory: "agent",
        config: {
          icon: "brain",
          provider: "google",
          model: "gemini-2.5-flash",
          systemPrompt:
            "You are the Ops Orchestrator (parity: agents/orchestrator_agent.py + agents/orchestrator.py). " +
            "Default handler for AutomationEdge ops: workflows, queues, schedules, agents, batch jobs. " +
            "Guide investigation, tool use, and next steps. If the user needs deep logs vs fixes vs RCA, " +
            "note which specialist would own it in the main app.",
          temperature: 0.45,
          maxTokens: 4096,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_11",
      type: "agenticNode",
      position: { x: 1420, y: 220 },
      data: {
        label: "Human Approval",
        nodeCategory: "action",
        config: {
          icon: "user-check",
          approvalMessage:
            "Approve remediation actions before they are persisted to conversation history (parity: gateway AWAITING_APPROVAL).",
          timeout: 86400,
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_12",
      type: "agenticNode",
      position: { x: 1680, y: 80 },
      data: {
        label: "Save Conversation State",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_7",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_13",
      type: "agenticNode",
      position: { x: 1680, y: 220 },
      data: {
        label: "Save Conversation State",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_8",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_14",
      type: "agenticNode",
      position: { x: 1680, y: 400 },
      data: {
        label: "Save Conversation State",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_9",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_15",
      type: "agenticNode",
      position: { x: 1680, y: 560 },
      data: {
        label: "Save Conversation State",
        nodeCategory: "action",
        config: {
          icon: "save",
          sessionIdExpression: "trigger.session_id",
          responseNodeId: "node_10",
          userMessageExpression: "trigger.message",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
  ],
  edges: [
    { id: "e_1_2", source: "node_1", target: "node_2" },
    { id: "e_2_3", source: "node_2", target: "node_3" },
    { id: "e_3_4", source: "node_3", target: "node_4" },
    {
      id: "e_4_7",
      source: "node_4",
      target: "node_7",
      sourceHandle: "true",
      label: "Yes",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_4_5",
      source: "node_4",
      target: "node_5",
      sourceHandle: "false",
      label: "No",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_5_8",
      source: "node_5",
      target: "node_8",
      sourceHandle: "true",
      label: "Yes",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_5_6",
      source: "node_5",
      target: "node_6",
      sourceHandle: "false",
      label: "No",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_6_9",
      source: "node_6",
      target: "node_9",
      sourceHandle: "true",
      label: "Yes",
      style: { stroke: "#22c55e", strokeWidth: 2 },
      animated: true,
    },
    {
      id: "e_6_10",
      source: "node_6",
      target: "node_10",
      sourceHandle: "false",
      label: "No",
      style: { stroke: "#ef4444", strokeWidth: 2 },
      animated: true,
    },
    { id: "e_7_12", source: "node_7", target: "node_12" },
    { id: "e_8_11", source: "node_8", target: "node_11" },
    { id: "e_11_13", source: "node_11", target: "node_13" },
    { id: "e_9_14", source: "node_9", target: "node_14" },
    { id: "e_10_15", source: "node_10", target: "node_15" },
  ],
};
