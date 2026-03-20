import { useCallback, useRef, type DragEvent } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type ReactFlowInstance,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useFlowStore } from "@/store/flowStore";
import { useWorkflowStore } from "@/store/workflowStore";
import { AgenticNode } from "@/components/nodes/AgenticNode";
import type { NodeCategory } from "@/types/nodes";

const nodeTypes = { agenticNode: AgenticNode };

export function FlowCanvas() {
  const reactFlowRef = useRef<ReactFlowInstance | null>(null);

  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);
  const onNodesChange = useFlowStore((s) => s.onNodesChange);
  const onEdgesChange = useFlowStore((s) => s.onEdgesChange);
  const onConnect = useFlowStore((s) => s.onConnect);
  const addNode = useFlowStore((s) => s.addNode);
  const selectNode = useFlowStore((s) => s.selectNode);
  const markDirty = useWorkflowStore((s) => s.markDirty);

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/reactflow");
      if (!raw || !reactFlowRef.current) return;

      const { nodeCategory, label, defaultConfig } = JSON.parse(raw) as {
        nodeCategory: NodeCategory;
        label: string;
        defaultConfig: Record<string, unknown>;
      };

      const position = reactFlowRef.current.screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });

      addNode(nodeCategory, label, position, defaultConfig);
      markDirty();
    },
    [addNode, markDirty],
  );

  const handleNodesChange: typeof onNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes);
      const hasMeaningfulChange = changes.some(
        (c) => c.type !== "select" && c.type !== "dimensions",
      );
      if (hasMeaningfulChange) markDirty();
    },
    [onNodesChange, markDirty],
  );

  const handleEdgesChange: typeof onEdgesChange = useCallback(
    (changes) => {
      onEdgesChange(changes);
      if (changes.length > 0) markDirty();
    },
    [onEdgesChange, markDirty],
  );

  const handleConnect: typeof onConnect = useCallback(
    (connection) => {
      onConnect(connection);
      markDirty();
    },
    [onConnect, markDirty],
  );

  return (
    <div className="flex-1 h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={handleConnect}
        onInit={(instance) => {
          reactFlowRef.current = instance;
        }}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onNodeClick={(_, node) => selectNode(node.id)}
        onPaneClick={() => selectNode(null)}
        nodeTypes={nodeTypes}
        fitView
        deleteKeyCode={["Backspace", "Delete"]}
        className="bg-background"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
        <Controls className="!bg-card !border !border-border !rounded-lg !shadow-sm" />
        <MiniMap
          className="!bg-card !border !border-border !rounded-lg"
          maskColor="rgba(0, 0, 0, 0.1)"
          pannable
          zoomable
        />
      </ReactFlow>
    </div>
  );
}
