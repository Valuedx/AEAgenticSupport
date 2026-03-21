import {
  CircleCheck,
  CircleX,
  CircleDot,
  Loader2,
  Pause,
  X,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  Maximize2,
  ClipboardCheck,
} from "lucide-react";
import { useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorkflowStore } from "@/store/workflowStore";
import { HITLResumeDialog } from "@/components/toolbar/HITLResumeDialog";
import type { ExecutionLogOut } from "@/lib/api";

// ---------------------------------------------------------------------------
// CopyButton — clipboard copy with 2s checkmark confirmation
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      title={copied ? "Copied!" : "Copy to clipboard"}
      className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground shrink-0"
    >
      {copied ? (
        <Check className="h-3 w-3 text-green-500" />
      ) : (
        <Copy className="h-3 w-3" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// FullJsonDialog — full-size JSON viewer in a dialog
// ---------------------------------------------------------------------------

function FullJsonDialog({
  open,
  onClose,
  title,
  data,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  data: unknown;
}) {
  const json = JSON.stringify(data, null, 2);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl w-full">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="text-sm">{title}</DialogTitle>
            <CopyButton text={json} />
          </div>
        </DialogHeader>
        <ScrollArea className="max-h-[70vh]">
          <pre className="text-xs font-mono bg-muted rounded-md p-4 whitespace-pre-wrap break-all">
            {json}
          </pre>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// JsonBlock — collapsible JSON preview with copy + expand buttons
// ---------------------------------------------------------------------------

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const json = JSON.stringify(data, null, 2);

  return (
    <div>
      <div className="flex items-center gap-1 mb-0.5">
        <p className="text-[10px] font-medium text-muted-foreground flex-1">{label}</p>
        <CopyButton text={json} />
        <button
          onClick={() => setDialogOpen(true)}
          title="View full output"
          className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
        >
          <Maximize2 className="h-3 w-3" />
        </button>
      </div>
      <pre className="text-xs bg-muted rounded p-2 overflow-x-auto max-h-32 font-mono">
        {json}
      </pre>
      <FullJsonDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={label}
        data={data}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status maps
// ---------------------------------------------------------------------------

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
            <JsonBlock label="Output" data={log.output_json} />
          )}
          {log.input_json && (
            <JsonBlock label="Input" data={log.input_json} />
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
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const instanceContext = useWorkflowStore((s) => s.instanceContext);
  const fetchInstanceContext = useWorkflowStore((s) => s.fetchInstanceContext);
  const [hitlOpen, setHitlOpen] = useState(false);

  if (!activeInstance) return null;

  const isSuspended = activeInstance.status === "suspended";
  const Icon = STATUS_ICON[activeInstance.status] ?? CircleDot;
  const color = STATUS_COLOR[activeInstance.status] ?? "text-muted-foreground";

  const handleReviewResume = async () => {
    if (!currentWorkflow) return;
    await fetchInstanceContext(currentWorkflow.id, activeInstance.id);
    setHitlOpen(true);
  };

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
            streaming…
          </span>
        )}
        <div className="flex-1" />
        {isSuspended && currentWorkflow && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px] gap-1 text-yellow-700 border-yellow-300 hover:bg-yellow-50"
            onClick={handleReviewResume}
          >
            <ClipboardCheck className="h-3 w-3" />
            Review &amp; Resume
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0"
          onClick={clearExecution}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {currentWorkflow && instanceContext && (
        <HITLResumeDialog
          open={hitlOpen}
          onClose={() => setHitlOpen(false)}
          workflowId={currentWorkflow.id}
          instanceId={activeInstance.id}
          context={instanceContext}
        />
      )}
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
