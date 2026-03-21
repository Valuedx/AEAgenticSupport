import { create } from "zustand";
import type { Edge, Node } from "@xyflow/react";
import {
  api,
  type WorkflowOut,
  type InstanceDetailOut,
  type InstanceContextOut,
} from "@/lib/api";
import { useFlowStore } from "@/store/flowStore";
import { EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW } from "@/lib/exampleComplexWorkflow";
import { EXAMPLE_AUTOMATIONEDGE_MAIN_WORKFLOW } from "@/lib/exampleMainAppWorkflow";

interface WorkflowState {
  currentWorkflow: WorkflowOut | null;
  workflows: WorkflowOut[];
  isDirty: boolean;

  activeInstance: InstanceDetailOut | null;
  isExecuting: boolean;

  /** Context snapshot loaded for HITL review of a suspended instance. */
  instanceContext: InstanceContextOut | null;

  /**
   * Live streaming token buffer per node_id.
   * Cleared when execution starts; accumulated as ``token`` SSE events arrive.
   * When a node's ``done: true`` message arrives the buffer is preserved
   * (the final LLM response will overwrite it via the log event shortly after).
   */
  streamingTokens: Record<string, string>;

  loading: boolean;
  error: string | null;

  _sseCleanup: (() => void) | null;

  fetchWorkflows: () => Promise<void>;
  loadWorkflow: (id: string) => Promise<void>;
  saveWorkflow: (name?: string) => Promise<void>;
  deleteWorkflow: (id: string) => Promise<void>;
  newWorkflow: () => void;
  loadExampleComplexWorkflow: () => void;
  loadAutomationEdgeMainWorkflow: () => void;
  markDirty: () => void;

  executeWorkflow: (triggerPayload?: Record<string, unknown>) => Promise<void>;
  retryInstance: (workflowId: string, instanceId: string, fromNodeId?: string) => Promise<void>;
  /** Fetch and cache the context snapshot for a suspended instance. */
  fetchInstanceContext: (workflowId: string, instanceId: string) => Promise<void>;
  /** Resume a suspended instance with optional approval payload and context patch. */
  resumeInstance: (
    workflowId: string,
    instanceId: string,
    approvalPayload: Record<string, unknown>,
    contextPatch?: Record<string, unknown>,
  ) => Promise<void>;
  pollInstance: (workflowId: string, instanceId: string) => Promise<void>;
  streamInstance: (workflowId: string, instanceId: string) => void;
  clearExecution: () => void;
}

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  currentWorkflow: null,
  workflows: [],
  isDirty: false,
  activeInstance: null,
  isExecuting: false,
  instanceContext: null,
  streamingTokens: {},
  loading: false,
  error: null,
  _sseCleanup: null,

  fetchWorkflows: async () => {
    set({ loading: true, error: null });
    try {
      const workflows = await api.listWorkflows();
      set({ workflows, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  loadWorkflow: async (id) => {
    set({ loading: true, error: null });
    try {
      const wf = await api.getWorkflow(id);
      const graph = wf.graph_json;
      const newNodes = (graph.nodes ?? []) as Node[];
      const newEdges = (graph.edges ?? []) as Edge[];

      useFlowStore.getState().replaceGraph(newNodes, newEdges);

      set({ currentWorkflow: wf, isDirty: false, loading: false, activeInstance: null });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  saveWorkflow: async (name) => {
    set({ loading: true, error: null });
    try {
      const { nodes, edges } = useFlowStore.getState();
      const graph_json = { nodes, edges };
      const current = get().currentWorkflow;

      let wf: WorkflowOut;
      if (current) {
        wf = await api.updateWorkflow(current.id, {
          name: name ?? current.name,
          graph_json,
        });
      } else {
        wf = await api.createWorkflow({
          name: name || "Untitled Workflow",
          graph_json,
        });
      }

      set({ currentWorkflow: wf, isDirty: false, loading: false });
      get().fetchWorkflows();
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  deleteWorkflow: async (id) => {
    set({ loading: true, error: null });
    try {
      await api.deleteWorkflow(id);
      const current = get().currentWorkflow;
      if (current?.id === id) {
        get().newWorkflow();
      }
      set({ loading: false });
      get().fetchWorkflows();
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  newWorkflow: () => {
    useFlowStore.getState().replaceGraph([], []);
    set({ currentWorkflow: null, isDirty: false, activeInstance: null });
  },

  loadExampleComplexWorkflow: () => {
    const { nodes, edges } = EXAMPLE_IT_SUPPORT_HELPDESK_WORKFLOW;
    useFlowStore.getState().replaceGraph(nodes, edges);
    set({
      currentWorkflow: null,
      isDirty: true,
      activeInstance: null,
      error: null,
    });
  },

  loadAutomationEdgeMainWorkflow: () => {
    const { nodes, edges } = EXAMPLE_AUTOMATIONEDGE_MAIN_WORKFLOW;
    useFlowStore.getState().replaceGraph(nodes, edges);
    set({
      currentWorkflow: null,
      isDirty: true,
      activeInstance: null,
      error: null,
    });
  },

  markDirty: () => {
    set({ isDirty: true });
  },

  executeWorkflow: async (triggerPayload) => {
    const wf = get().currentWorkflow;
    if (!wf) return;

    set({ isExecuting: true, error: null, streamingTokens: {} });
    try {
      if (get().isDirty) {
        await get().saveWorkflow();
      }
      const instance = await api.executeWorkflow(wf.id, triggerPayload);
      set({
        activeInstance: { ...instance, logs: [] },
        isExecuting: true,
      });
      get().streamInstance(wf.id, instance.id);
    } catch (e) {
      set({ error: String(e), isExecuting: false });
    }
  },

  retryInstance: async (workflowId, instanceId, fromNodeId) => {
    set({ isExecuting: true, error: null });
    try {
      const instance = await api.retryInstance(workflowId, instanceId, fromNodeId);
      set({
        activeInstance: { ...instance, logs: [] },
        isExecuting: true,
      });
      get().streamInstance(workflowId, instance.id);
    } catch (e) {
      set({ error: String(e), isExecuting: false });
    }
  },

  streamInstance: (workflowId, instanceId) => {
    const prev = get()._sseCleanup;
    if (prev) prev();

    const cleanup = api.streamInstance(
      workflowId,
      instanceId,
      (log) => {
        const inst = get().activeInstance;
        if (!inst) return;
        const existing = inst.logs.find((l) => l.id === log.id);
        const logs = existing
          ? inst.logs.map((l) => (l.id === log.id ? { ...l, ...log } : l))
          : [...inst.logs, log as InstanceDetailOut["logs"][number]];
        set({ activeInstance: { ...inst, logs } });
      },
      (status) => {
        const inst = get().activeInstance;
        if (!inst) return;
        set({ activeInstance: { ...inst, status: status.instance_status, current_node_id: status.current_node_id ?? inst.current_node_id } });
      },
      () => {
        set({ isExecuting: false, _sseCleanup: null, streamingTokens: {} });
        const wf = get().currentWorkflow;
        const inst = get().activeInstance;
        if (wf && inst) {
          api.getInstanceDetail(wf.id, inst.id).then((detail) => {
            set({ activeInstance: detail });
          }).catch(() => {});
        }
      },
      (tokenEvent) => {
        if (tokenEvent.done) return; // keep buffer; log event will overwrite shortly
        set((state) => ({
          streamingTokens: {
            ...state.streamingTokens,
            [tokenEvent.node_id]: (state.streamingTokens[tokenEvent.node_id] ?? "") + tokenEvent.token,
          },
        }));
      },
    );

    set({ _sseCleanup: cleanup });
  },

  fetchInstanceContext: async (workflowId, instanceId) => {
    try {
      const ctx = await api.getInstanceContext(workflowId, instanceId);
      set({ instanceContext: ctx });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  resumeInstance: async (workflowId, instanceId, approvalPayload, contextPatch) => {
    set({ isExecuting: true, error: null, instanceContext: null });
    try {
      const instance = await api.callbackWorkflow(workflowId, approvalPayload, contextPatch);
      set({
        activeInstance: { ...instance, logs: get().activeInstance?.logs ?? [] },
        isExecuting: true,
      });
      get().streamInstance(workflowId, instance.id);
    } catch (e) {
      set({ error: String(e), isExecuting: false });
    }
  },

  pollInstance: async (workflowId, instanceId) => {
    const poll = async () => {
      try {
        const detail = await api.getInstanceDetail(workflowId, instanceId);
        set({ activeInstance: detail });

        if (["completed", "failed", "suspended"].includes(detail.status)) {
          set({ isExecuting: false });
          return;
        }
        setTimeout(poll, 1500);
      } catch {
        set({ isExecuting: false });
      }
    };
    poll();
  },

  clearExecution: () => {
    const prev = get()._sseCleanup;
    if (prev) prev();
    set({ activeInstance: null, isExecuting: false, _sseCleanup: null });
  },
}));
