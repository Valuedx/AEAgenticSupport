import {
  CircleCheck,
  CircleX,
  CircleDot,
  Loader2,
  Pause,
  X,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useWorkflowStore } from "@/store/workflowStore";
import type { ExecutionLogOut } from "@/lib/api";

const STATUS_ICON: Record<string, typeof CircleDot> = {
  pending: CircleDot,
  running: Loader2,
  completed: CircleCheck,
  failed: CircleX,
  suspended: Pause,
};

const STATUS_COLOR: Record<string, string> = {
  pending: "text-muted-foreground",
  running: "text-blue-500",
  completed: "text-green-500",
  failed: "text-red-500",
  suspended: "text-yellow-500",
};

function LogEntry({ log }: { log: ExecutionLogOut }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = STATUS_ICON[log.status] ?? CircleDot;
  const color = STATUS_COLOR[log.status] ?? "text-muted-foreground";

  const duration =
    log.started_at && log.completed_at
      ? `${((new Date(log.completed_at).getTime() - new Date(log.started_at).getTime()) / 1000).toFixed(1)}s`
      : null;

  return (
    <div className="border rounded-md">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-accent/50 transition-colors"
      >
        <Icon
          className={`h-3.5 w-3.5 shrink-0 ${color} ${log.status === "running" ? "animate-spin" : ""}`}
        />
        <span className="text-sm font-medium truncate flex-1">
          {log.node_type}
        </span>
        {duration && (
          <span className="text-[10px] text-muted-foreground shrink-0">{duration}</span>
        )}
        <Badge
          variant="outline"
          className={`text-[10px] px-1 py-0 shrink-0 ${color}`}
        >
          {log.status}
        </Badge>
        {expanded ? (
          <ChevronUp className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1.5">
          <Separator />
          {log.error && (
            <div className="text-xs text-red-500 bg-red-50 dark:bg-red-950/30 rounded p-2 font-mono whitespace-pre-wrap">
              {log.error}
            </div>
          )}
          {log.output_json && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground mb-0.5">Output</p>
              <pre className="text-xs bg-muted rounded p-2 overflow-x-auto max-h-32 font-mono">
                {JSON.stringify(log.output_json, null, 2)}
              </pre>
            </div>
          )}
          {log.input_json && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground mb-0.5">Input</p>
              <pre className="text-xs bg-muted rounded p-2 overflow-x-auto max-h-32 font-mono">
                {JSON.stringify(log.input_json, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ExecutionPanel() {
  const activeInstance = useWorkflowStore((s) => s.activeInstance);
  const isExecuting = useWorkflowStore((s) => s.isExecuting);
  const clearExecution = useWorkflowStore((s) => s.clearExecution);

  if (!activeInstance) return null;

  const Icon = STATUS_ICON[activeInstance.status] ?? CircleDot;
  const color = STATUS_COLOR[activeInstance.status] ?? "text-muted-foreground";

  return (
    <div className="absolute bottom-0 left-0 right-0 bg-card border-t shadow-lg z-10 max-h-[45%] flex flex-col">
      <div className="flex items-center gap-2 px-4 py-2 shrink-0">
        <Icon
          className={`h-4 w-4 ${color} ${activeInstance.status === "running" ? "animate-spin" : ""}`}
        />
        <span className="text-sm font-medium">Execution</span>
        <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${color}`}>
          {activeInstance.status}
        </Badge>
        {isExecuting && (
          <span className="text-[10px] text-muted-foreground">
            polling...
          </span>
        )}
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0"
          onClick={clearExecution}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 px-4 py-2">
        <div className="space-y-1.5">
          {activeInstance.logs.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              {isExecuting ? "Waiting for execution logs..." : "No execution logs yet."}
            </p>
          ) : (
            activeInstance.logs.map((log) => (
              <LogEntry key={log.id} log={log} />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
