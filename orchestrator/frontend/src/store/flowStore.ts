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

interface FlowState {
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;

  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;

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
    set({ edges: addEdge(connection, get().edges) });
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
