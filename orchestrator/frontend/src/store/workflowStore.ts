import { create } from "zustand";
import {
  api,
  type WorkflowOut,
  type InstanceDetailOut,
} from "@/lib/api";
import { useFlowStore } from "@/store/flowStore";

interface WorkflowState {
  currentWorkflow: WorkflowOut | null;
  workflows: WorkflowOut[];
  isDirty: boolean;

  activeInstance: InstanceDetailOut | null;
  isExecuting: boolean;

  loading: boolean;
  error: string | null;

  _sseCleanup: (() => void) | null;

  fetchWorkflows: () => Promise<void>;
  loadWorkflow: (id: string) => Promise<void>;
  saveWorkflow: (name?: string) => Promise<void>;
  deleteWorkflow: (id: string) => Promise<void>;
  newWorkflow: () => void;
  markDirty: () => void;

  executeWorkflow: (triggerPayload?: Record<string, unknown>) => Promise<void>;
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
      const flow = useFlowStore.getState();
      const graph = wf.graph_json;
      flow.onNodesChange(
        flow.nodes.map((n) => ({ type: "remove" as const, id: n.id })),
      );
      flow.onEdgesChange(
        flow.edges.map((e) => ({ type: "remove" as const, id: e.id })),
      );

      const newNodes = (graph.nodes ?? []) as Parameters<typeof flow.onNodesChange>[0] extends (infer _) ? typeof flow.nodes : never;
      const newEdges = (graph.edges ?? []) as typeof flow.edges;

      useFlowStore.setState({
        nodes: newNodes,
        edges: newEdges,
        selectedNodeId: null,
      });

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
    useFlowStore.setState({ nodes: [], edges: [], selectedNodeId: null });
    set({ currentWorkflow: null, isDirty: false, activeInstance: null });
  },

  markDirty: () => {
    set({ isDirty: true });
  },

  executeWorkflow: async (triggerPayload) => {
    const wf = get().currentWorkflow;
    if (!wf) return;

    set({ isExecuting: true, error: null });
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
        set({ isExecuting: false, _sseCleanup: null });
        const wf = get().currentWorkflow;
        const inst = get().activeInstance;
        if (wf && inst) {
          api.getInstanceDetail(wf.id, inst.id).then((detail) => {
            set({ activeInstance: detail });
          }).catch(() => {});
        }
      },
    );

    set({ _sseCleanup: cleanup });
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
