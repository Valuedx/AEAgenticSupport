import type { Edge, Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";

/**
 * Parity sketch for the **AutomationEdge main app** routing story (`gateway/message_gateway.py`,
 * `agents/agent_router.py`): multi-agent router → diagnostic / remediation / RCA / ops specialists,
 * with **Human Approval** before persisting risky remediation (same idea as gateway `AWAITING_APPROVAL`).
 *
 * This DAG encodes that **routing intent** using **LLM Router** + **Condition** chains and
 * **Load/Save Conversation State** (orchestrator V0.9.x stateful DAGs). Attach MCP tools on the
 * ReAct nodes to mirror specialist tool categories.
 *
 * ## AI Studio + MS Teams (production-shaped path)
 *
 * Save this graph in **AE AI Hub**, copy the workflow **UUID**, and invoke it from AI Studio **without
 * spending LLM tokens in Studio** by setting `user_metadata.orchestrator_workflow_id` to that UUID
 * (see `gateway/message_gateway.py` and `orchestrator/TECHNICAL_BLUEPRINT.md` §10).
 *
 * Typical flow: **Teams** → Azure Bot → AI Studio extension / `handle_chat_message()` → bridge
 * `POST …/execute` with a merged trigger. `orchestrator_payload` (optional) is shallow-merged; if
 * you omit `message`, `session_id`, `user_id`, `user_role`, `user_name`, or `user_email`, Studio
 * fills them from the chat turn (`conversation_id` maps to `session_id`; user profile fields when wired).
 *
 * **Async by default:** the bridge may enqueue and return instance id + context URL (no long block in
 * chat). For **Teams**, set `orchestrator_wait_for_result: true` (or env
 * `ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT=true`) so the bot can return **one message** with the specialist
 * answer: this graph uses **Bridge User Reply** so `orchestrator_user_reply` is explicit; otherwise
 * the gateway can pick the longest LLM/ReAct `response` (`auto` mode). Use
 * `orchestrator_chat_reply_mode: "full_context"` for JSON-only. **Human-in-the-loop**
 * suspensions are **not** auto-resumed from Teams — use the Hub **Review & Resume** UI or
 * `POST /api/v1/workflows/{id}/callback`.
 *
 * Each specialist branch includes **Bridge User Reply** before save: it sets `orchestrator_user_reply`
 * so the bridge returns that exact text in sync mode (overrides heuristic `auto` extraction).
 *
 * The **Webhook Trigger** is for local **Run** in the builder; in Studio/Teams mode the same
 * fields arrive as `trigger.*` via the merged execute payload.
 *
 * Trigger JSON (Execute in UI, or equivalent merged `orchestrator_payload`):
 * {
 *   "session_id": "teams-thread-or-ae-session-001",
 *   "message": "Workflow 2887 failed — pull the error logs and suggest a fix",
 *   "user_role": "technical",
 *   "user_id": "user@company.com",
 *   "user_name": "Jane Doe",
 *   "user_email": "jane@company.com"
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
        displayName: "Ops intake (webhook or Studio)",
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
        displayName: "Load session history",
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
        displayName: "Route message to specialist",
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
        displayName: "If intent = diagnostics",
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
        displayName: "Else if intent = remediation",
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
        displayName: "Else if intent = RCA report",
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
        displayName: "Diagnostics specialist (ReAct + tools)",
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
        displayName: "Remediation specialist (ReAct + tools)",
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
        displayName: "RCA report author",
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
        displayName: "Default ops orchestrator",
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
        displayName: "Human gate: approve remediation",
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
        displayName: "Save turn · diagnostics answer",
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
        displayName: "Save turn · remediation (after approval)",
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
        displayName: "Save turn · RCA answer",
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
        displayName: "Save turn · default ops answer",
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
    {
      id: "node_16",
      type: "agenticNode",
      position: { x: 1320, y: 80 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · diagnostics",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_7",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_17",
      type: "agenticNode",
      position: { x: 1280, y: 220 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · remediation",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_8",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_18",
      type: "agenticNode",
      position: { x: 1540, y: 400 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · RCA",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_9",
          messageExpression: "",
        },
        status: "idle",
      } satisfies AgenticNodeData,
    },
    {
      id: "node_19",
      type: "agenticNode",
      position: { x: 1540, y: 560 },
      data: {
        label: "Bridge User Reply",
        displayName: "Chat reply · default ops",
        nodeCategory: "action",
        config: {
          icon: "message-square",
          responseNodeId: "node_10",
          messageExpression: "",
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
    { id: "e_7_16", source: "node_7", target: "node_16" },
    { id: "e_16_12", source: "node_16", target: "node_12" },
    { id: "e_8_17", source: "node_8", target: "node_17" },
    { id: "e_17_11", source: "node_17", target: "node_11" },
    { id: "e_11_13", source: "node_11", target: "node_13" },
    { id: "e_9_18", source: "node_9", target: "node_18" },
    { id: "e_18_14", source: "node_18", target: "node_14" },
    { id: "e_10_19", source: "node_10", target: "node_19" },
    { id: "e_19_15", source: "node_19", target: "node_15" },
  ],
};
