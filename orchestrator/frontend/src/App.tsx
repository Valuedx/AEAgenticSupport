import { useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NodePalette } from "@/components/sidebar/NodePalette";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyInspector } from "@/components/sidebar/PropertyInspector";

export default function App() {
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);

  return (
    <TooltipProvider>
      <ReactFlowProvider>
        <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
          <NodePalette
            collapsed={paletteCollapsed}
            onToggle={() => setPaletteCollapsed((p) => !p)}
          />
          <FlowCanvas />
          <PropertyInspector />
        </div>
      </ReactFlowProvider>
    </TooltipProvider>
  );
}
