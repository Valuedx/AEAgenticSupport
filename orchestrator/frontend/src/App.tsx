import { useState, useCallback, useEffect, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NodePalette } from "@/components/sidebar/NodePalette";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyInspector } from "@/components/sidebar/PropertyInspector";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { Toolbar } from "@/components/toolbar/Toolbar";
import { ExecutionPanel } from "@/components/toolbar/ExecutionPanel";
import { LoginPage } from "@/components/auth/LoginPage";
import { ChevronLeft, ChevronRight, GripVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWorkflowStore } from "@/store/workflowStore";

const AUTH_MODE = import.meta.env.VITE_AUTH_MODE;

export default function App() {
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);
  const [paletteWidth, setPaletteWidth] = useState(256);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [rightWidth, setRightWidth] = useState(320);
  const [executionHeight, setExecutionHeight] = useState(250);
  const [isResizingExecution, setIsResizingExecution] = useState(false);
  const [isResizingPalette, setIsResizingPalette] = useState(false);
  const [isResizingRight, setIsResizingRight] = useState(false);
  // % of right sidebar height given to Property Inspector (rest goes to Chat)
  const [chatSplitPct, setChatSplitPct] = useState(40); // 40% props, 60% chat
  const [isResizingChat, setIsResizingChat] = useState(false);
  const rightSidebarRef = useRef<HTMLDivElement>(null);

  // Auth gate
  if (AUTH_MODE === "oidc" && !localStorage.getItem("ae_access_token")) {
    return <LoginPage />;
  }

  // Resizing logic
  const onMouseMove = useCallback((e: MouseEvent) => {
    if (isResizingExecution) {
      const newHeight = window.innerHeight - e.clientY;
      setExecutionHeight(Math.max(48, Math.min(newHeight, window.innerHeight - 100)));
    }
    if (isResizingPalette) {
      setPaletteWidth(Math.max(48, Math.min(e.clientX, 500)));
    }
    if (isResizingRight) {
      const newWidth = window.innerWidth - e.clientX;
      setRightWidth(Math.max(200, Math.min(newWidth, 600)));
    }
    if (isResizingChat && rightSidebarRef.current) {
      const rect = rightSidebarRef.current.getBoundingClientRect();
      const relY = e.clientY - rect.top;
      const pct = Math.round((relY / rect.height) * 100);
      setChatSplitPct(Math.max(15, Math.min(pct, 85)));
    }
  }, [isResizingExecution, isResizingPalette, isResizingRight, isResizingChat]);

  const stopResizing = useCallback(() => {
    setIsResizingExecution(false);
    setIsResizingPalette(false);
    setIsResizingRight(false);
    setIsResizingChat(false);
  }, []);

  useEffect(() => {
    if (isResizingExecution || isResizingPalette || isResizingRight || isResizingChat) {
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", stopResizing);
    }
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", stopResizing);
    };
  }, [isResizingExecution, isResizingPalette, isResizingRight, isResizingChat, onMouseMove, stopResizing]);

  // Auto-expand execution panel when a workflow is triggered from chat
  const isExecuting = useWorkflowStore((s) => s.isExecuting);
  useEffect(() => {
    if (isExecuting) {
      setExecutionHeight(h => Math.max(h, 300));
      setRightCollapsed(false); // ensure chat panel stays visible
    }
  }, [isExecuting]);

  return (
    <TooltipProvider>
      <ReactFlowProvider>
        <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-foreground">
          <Toolbar />
          
          <div className="flex flex-1 min-h-0 relative">
            {/* Left Sidebar */}
            <div 
              style={{ width: paletteCollapsed ? 48 : paletteWidth }} 
              className="relative flex flex-col h-full min-h-0 border-r bg-sidebar transition-[width] duration-300 ease-in-out shrink-0"
            >
              <div className="flex-1 min-h-0 overflow-hidden relative">
                <NodePalette
                  collapsed={paletteCollapsed}
                  onToggle={() => setPaletteCollapsed((p) => !p)}
                />
                {!paletteCollapsed && (
                  <div
                    className="absolute right-0 top-0 w-1 h-full hover:bg-primary/50 cursor-ew-resize transition-colors z-50"
                    onMouseDown={() => setIsResizingPalette(true)}
                  />
                )}
              </div>
            </div>

            {/* Main Canvas + Bottom Execution Panel */}
            <div className="flex-1 relative flex flex-col min-w-0 h-full">
              <div className="flex-1 relative min-h-0">
                <FlowCanvas />
              </div>

              {/* Resize Handle */}
              <div
                className="h-1 w-full bg-border hover:bg-primary/50 cursor-ns-resize transition-colors z-50 shrink-0"
                onMouseDown={() => setIsResizingExecution(true)}
              />

              {/* Bottom Panel */}
              <div 
                style={{ height: executionHeight }} 
                className="relative bg-card border-t shadow-lg shrink-0 min-h-0 overflow-hidden"
              >
                <ExecutionPanel onClear={() => setExecutionHeight(250)} />
              </div>
            </div>

            {/* Right Sidebar */}
            <div 
              ref={rightSidebarRef}
              style={{ width: rightCollapsed ? 40 : rightWidth }} 
              className="relative flex flex-col h-full min-h-0 border-l bg-sidebar transition-[width] duration-300 ease-in-out shrink-0"
            >
              {/* Resize Handle (Left side of right sidebar) */}
              {!rightCollapsed && (
                <div
                  className="absolute left-0 top-0 w-1 h-full hover:bg-primary/50 cursor-ew-resize transition-colors z-50"
                  onMouseDown={() => setIsResizingRight(true)}
                />
              )}

              <button
                onClick={() => setRightCollapsed(!rightCollapsed)}
                className="absolute -left-3 top-1/2 -translate-y-1/2 h-6 w-6 rounded-full border bg-background shadow-sm flex items-center justify-center z-50 hover:bg-accent transition-colors"
                title={rightCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
              >
                {rightCollapsed ? <ChevronLeft className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </button>

              <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                {!rightCollapsed ? (
                  <div className="flex flex-col flex-1 min-h-0">
                    {/* PropertyInspector - resizable top portion */}
                    <div 
                      style={{ height: `${chatSplitPct}%` }}
                      className="min-h-0 overflow-hidden shrink-0"
                    >
                      <PropertyInspector />
                    </div>

                    {/* Drag handle between Properties and Chat */}
                    <div
                      className="flex items-center justify-center h-3 bg-border/60 hover:bg-primary/30 cursor-ns-resize transition-colors shrink-0 select-none border-y"
                      onMouseDown={() => setIsResizingChat(true)}
                      title="Drag to resize"
                    >
                      <GripVertical className="h-3 w-3 rotate-90 text-muted-foreground" />
                    </div>

                    {/* ChatPanel - takes remaining space */}
                    <div className="flex-1 min-h-0 overflow-hidden">
                      <ChatPanel />
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center py-4 w-full gap-4 opacity-50">
                     <div className="w-1 h-20 bg-muted rounded-full" />
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </ReactFlowProvider>
    </TooltipProvider>
  );
}
