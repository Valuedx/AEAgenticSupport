import { useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NodePalette } from "@/components/sidebar/NodePalette";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyInspector } from "@/components/sidebar/PropertyInspector";
import { Toolbar } from "@/components/toolbar/Toolbar";
import { ExecutionPanel } from "@/components/toolbar/ExecutionPanel";

export default function App() {
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);

  return (
    <TooltipProvider>
      <ReactFlowProvider>
        <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-foreground">
          <Toolbar />
          <div className="flex flex-1 min-h-0">
            <NodePalette
              collapsed={paletteCollapsed}
              onToggle={() => setPaletteCollapsed((p) => !p)}
            />
            <div className="flex-1 relative">
              <FlowCanvas />
              <ExecutionPanel />
            </div>
            <PropertyInspector />
          </div>
        </div>
      </ReactFlowProvider>
    </TooltipProvider>
  );
}
