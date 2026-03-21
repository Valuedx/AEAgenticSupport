import { create } from "zustand";
import {
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type XYPosition,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
} from "@xyflow/react";
import type { AgenticNodeData, NodeCategory } from "@/types/nodes";

let nodeIdCounter = 0;
const nextId = () => `node_${++nodeIdCounter}`;

function syncNodeIdCounterFromNodes(nodes: Node[]) {
  let max = 0;
  for (const n of nodes) {
    const m = /^node_(\d+)$/.exec(n.id);
    if (m) max = Math.max(max, Number.parseInt(m[1], 10));
  }
  nodeIdCounter = max;
}

interface FlowState {
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;

  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;

  /** Replace canvas state and align `node_*` id counter for new nodes from the palette. */
  replaceGraph: (nodes: Node[], edges: Edge[]) => void;

  addNode: (
    nodeCategory: NodeCategory,
    label: string,
    position: XYPosition,
    defaultConfig?: Record<string, unknown>,
  ) => void;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, data: Partial<AgenticNodeData>) => void;
  deleteNode: (id: string) => void;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,

  onNodesChange: (changes) => {
    set({ nodes: applyNodeChanges(changes, get().nodes) });
  },

  onEdgesChange: (changes) => {
    set({ edges: applyEdgeChanges(changes, get().edges) });
  },

  onConnect: (connection) => {
    const sourceNode = get().nodes.find((n) => n.id === connection.source);
    const isCondition =
      sourceNode?.data?.nodeCategory === "logic" &&
      sourceNode?.data?.label === "Condition";

    const edge = {
      ...connection,
      label: isCondition ? (connection.sourceHandle === "false" ? "No" : "Yes") : undefined,
      style: isCondition
        ? { stroke: connection.sourceHandle === "false" ? "#ef4444" : "#22c55e", strokeWidth: 2 }
        : undefined,
      animated: isCondition ? true : false,
    };
    set({ edges: addEdge(edge, get().edges) });
  },

  replaceGraph: (nodes, edges) => {
    syncNodeIdCounterFromNodes(nodes);
    set({ nodes, edges, selectedNodeId: null });
  },

  addNode: (nodeCategory, label, position, defaultConfig = {}) => {
    const id = nextId();
    const newNode: Node = {
      id,
      type: "agenticNode",
      position,
      data: {
        label,
        nodeCategory,
        config: { ...defaultConfig },
        status: "idle",
      } satisfies AgenticNodeData,
    };
    set({ nodes: [...get().nodes, newNode], selectedNodeId: id });
  },

  selectNode: (id) => {
    set({ selectedNodeId: id });
  },

  updateNodeData: (id, data) => {
    set({
      nodes: get().nodes.map((node) =>
        node.id === id
          ? { ...node, data: { ...node.data, ...data } }
          : node,
      ),
    });
  },

  deleteNode: (id) => {
    set({
      nodes: get().nodes.filter((n) => n.id !== id),
      edges: get().edges.filter((e) => e.source !== id && e.target !== id),
      selectedNodeId:
        get().selectedNodeId === id ? null : get().selectedNodeId,
    });
  },
}));
