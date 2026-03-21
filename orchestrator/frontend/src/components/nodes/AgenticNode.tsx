import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  Webhook,
  Clock,
  Brain,
  Repeat,
  Wrench,
  Globe,
  UserCheck,
  GitBranch,
  GitMerge,
  Route,
  History,
  Save,
  AlertCircle,
  AlertTriangle,
  type LucideIcon,
} from "lucide-react";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AgenticNodeData, NodeCategory } from "@/types/nodes";
import { cn } from "@/lib/utils";
import { useNodeValidation } from "@/lib/useNodeValidation";

const ICON_MAP: Record<string, LucideIcon> = {
  webhook: Webhook,
  clock: Clock,
  brain: Brain,
  repeat: Repeat,
  wrench: Wrench,
  globe: Globe,
  "user-check": UserCheck,
  "git-branch": GitBranch,
  "git-merge": GitMerge,
  route: Route,
  history: History,
  save: Save,
};

const CATEGORY_STYLES: Record<
  NodeCategory,
  { border: string; bg: string; badge: string }
> = {
  trigger: {
    border: "border-amber-500/60",
    bg: "bg-amber-50 dark:bg-amber-950/30",
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  },
  agent: {
    border: "border-violet-500/60",
    bg: "bg-violet-50 dark:bg-violet-950/30",
    badge:
      "bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-200",
  },
  action: {
    border: "border-sky-500/60",
    bg: "bg-sky-50 dark:bg-sky-950/30",
    badge: "bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-200",
  },
  logic: {
    border: "border-emerald-500/60",
    bg: "bg-emerald-50 dark:bg-emerald-950/30",
    badge:
      "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
  },
};

const STATUS_DOT: Record<string, string> = {
  idle: "bg-gray-400",
  running: "bg-blue-500 animate-pulse",
  completed: "bg-green-500",
  failed: "bg-red-500",
  suspended: "bg-yellow-500",
};

function AgenticNodeComponent({ id, data, selected }: NodeProps) {
  const nodeData = data as unknown as AgenticNodeData;
  const { label, nodeCategory, config, status = "idle" } = nodeData;
  const styles = CATEGORY_STYLES[nodeCategory];
  const iconName = (config?.icon as string) || getDefaultIcon(nodeCategory);
  const Icon = ICON_MAP[iconName] || Brain;

  const hasInput = nodeCategory !== "trigger";
  const hasOutput = nodeCategory !== "logic" || label !== "Merge";
  const isCondition = nodeCategory === "logic" && label === "Condition";

  // Design-time validation indicators
  const { errorIds, warningIds } = useNodeValidation();
  const hasError = errorIds.has(id);
  const hasWarning = !hasError && warningIds.has(id);

  return (
    <Card
      className={cn(
        "min-w-[180px] max-w-[220px] border-2 shadow-md transition-shadow",
        styles.border,
        styles.bg,
        // Selection ring takes highest priority
        selected && "ring-2 ring-primary shadow-lg",
        // Error ring when not selected
        !selected && hasError && "ring-2 ring-red-500/70",
        // Warning ring when not selected and no error
        !selected && hasWarning && "ring-2 ring-yellow-500/60",
      )}
    >
      {hasInput && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-3 !h-3 !bg-muted-foreground !border-2 !border-background"
        />
      )}

      <CardHeader className="p-3 pb-2">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-md p-1.5", styles.badge)}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="flex-1 min-w-0">
            <CardTitle className="text-sm font-medium truncate">
              {label}
            </CardTitle>
          </div>
          {hasError && (
            <AlertCircle className="h-3.5 w-3.5 shrink-0 text-red-500" title="This node has configuration errors" />
          )}
          {hasWarning && (
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-yellow-500" title="This node is not connected to a trigger" />
          )}
          {!hasError && !hasWarning && (
            <span className={cn("h-2 w-2 rounded-full shrink-0", STATUS_DOT[status])} />
          )}
        </div>
        <div className="flex items-center gap-1.5 mt-1.5">
          <Badge variant="outline" className={cn("text-[10px] px-1.5 py-0", styles.badge)}>
            {nodeCategory}
          </Badge>
          {nodeCategory === "agent" && config?.model != null && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {String(config.model)}
            </Badge>
          )}
        </div>
      </CardHeader>

      {isCondition ? (
        <>
          <div className="absolute right-[-4px] text-[8px] font-bold text-green-600 dark:text-green-400" style={{ top: "25%", transform: "translateX(100%) translateY(-50%)", paddingLeft: 6 }}>
            Yes
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="true"
            className="!w-3 !h-3 !bg-green-500 !border-2 !border-background"
            style={{ top: "35%" }}
          />
          <div className="absolute right-[-4px] text-[8px] font-bold text-red-600 dark:text-red-400" style={{ top: "57%", transform: "translateX(100%) translateY(-50%)", paddingLeft: 6 }}>
            No
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="false"
            className="!w-3 !h-3 !bg-red-500 !border-2 !border-background"
            style={{ top: "65%" }}
          />
        </>
      ) : (
        hasOutput && (
          <Handle
            type="source"
            position={Position.Right}
            className="!w-3 !h-3 !bg-muted-foreground !border-2 !border-background"
          />
        )
      )}
    </Card>
  );
}

function getDefaultIcon(category: NodeCategory): string {
  switch (category) {
    case "trigger":
      return "webhook";
    case "agent":
      return "brain";
    case "action":
      return "wrench";
    case "logic":
      return "git-branch";
  }
}

export const AgenticNode = memo(AgenticNodeComponent);
