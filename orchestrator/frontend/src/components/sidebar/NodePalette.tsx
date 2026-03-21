import { useState, type DragEvent } from "react";
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
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { NODE_PALETTE, type PaletteItem, type NodeCategory } from "@/types/nodes";
import { cn } from "@/lib/utils";

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

const CATEGORY_META: Record<NodeCategory, { label: string; color: string }> = {
  trigger: { label: "Triggers", color: "text-amber-600 dark:text-amber-400" },
  agent: { label: "AI Agents", color: "text-violet-600 dark:text-violet-400" },
  action: { label: "Actions", color: "text-sky-600 dark:text-sky-400" },
  logic: { label: "Logic", color: "text-emerald-600 dark:text-emerald-400" },
};

const CATEGORIES: NodeCategory[] = ["trigger", "agent", "action", "logic"];

function DraggableItem({ item }: { item: PaletteItem }) {
  const Icon = ICON_MAP[item.icon] || Wrench;

  const onDragStart = (e: DragEvent) => {
    e.dataTransfer.setData(
      "application/reactflow",
      JSON.stringify({
        nodeCategory: item.nodeCategory,
        label: item.label,
        defaultConfig: { ...item.defaultConfig, icon: item.icon },
      }),
    );
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="flex items-center gap-2.5 rounded-md border bg-card px-3 py-2 cursor-grab active:cursor-grabbing hover:bg-accent transition-colors"
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <p className="text-sm font-medium leading-tight truncate">{item.label}</p>
        <p className="text-[11px] text-muted-foreground leading-tight truncate">
          {item.description}
        </p>
      </div>
    </div>
  );
}

interface NodePaletteProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function NodePalette({ collapsed, onToggle }: NodePaletteProps) {
  const [openCategories, setOpenCategories] = useState<Set<NodeCategory>>(
    new Set(CATEGORIES),
  );

  const toggle = (cat: NodeCategory) => {
    setOpenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  if (collapsed) {
    return (
      <div className="flex flex-col items-center w-12 border-r bg-sidebar py-3 gap-2">
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
          title="Expand palette"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <Separator />
        {NODE_PALETTE.slice(0, 6).map((item) => {
          const Icon = ICON_MAP[item.icon] || Wrench;
          return (
            <div
              key={item.label}
              draggable
              onDragStart={(e: DragEvent<HTMLDivElement>) => {
                e.dataTransfer.setData(
                  "application/reactflow",
                  JSON.stringify({
                    nodeCategory: item.nodeCategory,
                    label: item.label,
                    defaultConfig: { ...item.defaultConfig, icon: item.icon },
                  }),
                );
                e.dataTransfer.effectAllowed = "move";
              }}
              className="p-1.5 rounded-md cursor-grab hover:bg-accent transition-colors"
              title={item.label}
            >
              <Icon className="h-4 w-4 text-muted-foreground" />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="flex flex-col w-64 border-r bg-sidebar">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold">Node Palette</h2>
        <button
          onClick={onToggle}
          className="p-1 rounded-md hover:bg-accent transition-colors"
          title="Collapse palette"
        >
          <ChevronRight className="h-4 w-4 rotate-180" />
        </button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 px-3 py-2">
        <div className="space-y-1">
          {CATEGORIES.map((cat) => {
            const meta = CATEGORY_META[cat];
            const items = NODE_PALETTE.filter((i) => i.nodeCategory === cat);
            return (
              <Collapsible
                key={cat}
                open={openCategories.has(cat)}
                onOpenChange={() => toggle(cat)}
              >
                <CollapsibleTrigger className="flex items-center gap-2 w-full rounded-md px-2 py-1.5 text-sm font-medium hover:bg-accent transition-colors">
                  <ChevronRight
                    className={cn(
                      "h-3.5 w-3.5 transition-transform",
                      openCategories.has(cat) && "rotate-90",
                    )}
                  />
                  <span className={meta.color}>{meta.label}</span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {items.length}
                  </span>
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-1.5 py-1.5 pl-2">
                  {items.map((item) => (
                    <DraggableItem key={item.label} item={item} />
                  ))}
                </CollapsibleContent>
              </Collapsible>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
