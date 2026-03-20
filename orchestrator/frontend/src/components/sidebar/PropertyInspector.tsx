import { useFlowStore } from "@/store/flowStore";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";
import type { AgenticNodeData } from "@/types/nodes";

const LLM_MODELS = [
  { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "gpt-4o-mini", label: "GPT-4o Mini" },
  { value: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
  { value: "claude-3-5-haiku-20241022", label: "Claude 3.5 Haiku" },
];

const LLM_PROVIDERS = [
  { value: "google", label: "Google Vertex AI" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
];

function AgentConfigPanel({ data, onUpdate }: ConfigPanelProps) {
  const config = data.config;
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="provider">LLM Provider</Label>
        <Select
          value={String(config.provider || "google")}
          onValueChange={(v) => onUpdate({ config: { ...config, provider: v } })}
        >
          <SelectTrigger id="provider">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LLM_PROVIDERS.map((p) => (
              <SelectItem key={p.value} value={p.value}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="model">Model</Label>
        <Select
          value={String(config.model || "")}
          onValueChange={(v) => onUpdate({ config: { ...config, model: v } })}
        >
          <SelectTrigger id="model">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LLM_MODELS.map((m) => (
              <SelectItem key={m.value} value={m.value}>
                {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="systemPrompt">System Prompt</Label>
        <Textarea
          id="systemPrompt"
          rows={4}
          value={String(config.systemPrompt || "")}
          onChange={(e) =>
            onUpdate({ config: { ...config, systemPrompt: e.target.value } })
          }
          placeholder="You are a helpful assistant..."
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="temperature">Temperature</Label>
        <Input
          id="temperature"
          type="number"
          min={0}
          max={2}
          step={0.1}
          value={String(config.temperature ?? 0.7)}
          onChange={(e) =>
            onUpdate({ config: { ...config, temperature: parseFloat(e.target.value) } })
          }
        />
      </div>
    </div>
  );
}

function TriggerConfigPanel({ data, onUpdate }: ConfigPanelProps) {
  const config = data.config;
  return (
    <div className="space-y-4">
      {config.path !== undefined && (
        <div className="space-y-2">
          <Label htmlFor="path">Webhook Path</Label>
          <Input
            id="path"
            value={String(config.path || "")}
            onChange={(e) => onUpdate({ config: { ...config, path: e.target.value } })}
          />
        </div>
      )}
      {config.cron !== undefined && (
        <div className="space-y-2">
          <Label htmlFor="cron">Cron Expression</Label>
          <Input
            id="cron"
            value={String(config.cron || "")}
            onChange={(e) => onUpdate({ config: { ...config, cron: e.target.value } })}
            placeholder="0 * * * *"
          />
        </div>
      )}
    </div>
  );
}

function ActionConfigPanel({ data, onUpdate }: ConfigPanelProps) {
  const config = data.config;
  return (
    <div className="space-y-4">
      {config.toolName !== undefined && (
        <div className="space-y-2">
          <Label htmlFor="toolName">MCP Tool Name</Label>
          <Input
            id="toolName"
            value={String(config.toolName || "")}
            onChange={(e) =>
              onUpdate({ config: { ...config, toolName: e.target.value } })
            }
            placeholder="get_request_status"
          />
        </div>
      )}
      {config.url !== undefined && (
        <>
          <div className="space-y-2">
            <Label htmlFor="url">URL</Label>
            <Input
              id="url"
              value={String(config.url || "")}
              onChange={(e) => onUpdate({ config: { ...config, url: e.target.value } })}
              placeholder="https://api.example.com/..."
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="method">HTTP Method</Label>
            <Select
              value={String(config.method || "GET")}
              onValueChange={(v) => onUpdate({ config: { ...config, method: v } })}
            >
              <SelectTrigger id="method">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </>
      )}
      {config.approvalMessage !== undefined && (
        <div className="space-y-2">
          <Label htmlFor="approvalMsg">Approval Message</Label>
          <Textarea
            id="approvalMsg"
            rows={3}
            value={String(config.approvalMessage || "")}
            onChange={(e) =>
              onUpdate({ config: { ...config, approvalMessage: e.target.value } })
            }
            placeholder="Please review and approve..."
          />
        </div>
      )}
    </div>
  );
}

function LogicConfigPanel({ data, onUpdate }: ConfigPanelProps) {
  const config = data.config;
  return (
    <div className="space-y-4">
      {config.condition !== undefined && (
        <div className="space-y-2">
          <Label htmlFor="condition">Condition Expression</Label>
          <Input
            id="condition"
            value={String(config.condition || "")}
            onChange={(e) =>
              onUpdate({ config: { ...config, condition: e.target.value } })
            }
            placeholder='output.status === "success"'
          />
        </div>
      )}
      {config.strategy !== undefined && (
        <div className="space-y-2">
          <Label htmlFor="strategy">Merge Strategy</Label>
          <Select
            value={String(config.strategy || "waitAll")}
            onValueChange={(v) => onUpdate({ config: { ...config, strategy: v } })}
          >
            <SelectTrigger id="strategy">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="waitAll">Wait for All</SelectItem>
              <SelectItem value="waitAny">Wait for Any</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}

interface ConfigPanelProps {
  data: AgenticNodeData;
  onUpdate: (partial: Partial<AgenticNodeData>) => void;
}

export function PropertyInspector() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const nodes = useFlowStore((s) => s.nodes);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const selectNode = useFlowStore((s) => s.selectNode);
  const deleteNode = useFlowStore((s) => s.deleteNode);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);

  if (!selectedNode) {
    return (
      <div className="flex flex-col items-center justify-center w-72 border-l bg-sidebar text-muted-foreground p-6">
        <p className="text-sm text-center">
          Select a node on the canvas to inspect its properties.
        </p>
      </div>
    );
  }

  const data = selectedNode.data as AgenticNodeData;
  const onUpdate = (partial: Partial<AgenticNodeData>) =>
    updateNodeData(selectedNode.id, partial);

  const ConfigPanel =
    data.nodeCategory === "agent"
      ? AgentConfigPanel
      : data.nodeCategory === "trigger"
        ? TriggerConfigPanel
        : data.nodeCategory === "action"
          ? ActionConfigPanel
          : LogicConfigPanel;

  return (
    <div className="flex flex-col w-72 border-l bg-sidebar">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold">Properties</h2>
        <button
          onClick={() => selectNode(null)}
          className="p-1 rounded-md hover:bg-accent transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 px-4 py-3">
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="nodeLabel">Label</Label>
            <Input
              id="nodeLabel"
              value={data.label}
              onChange={(e) => onUpdate({ label: e.target.value })}
            />
          </div>

          <div className="flex items-center gap-2">
            <Badge variant="outline">{data.nodeCategory}</Badge>
            <Badge variant="secondary">{data.status || "idle"}</Badge>
          </div>

          <Separator />

          <ConfigPanel data={data} onUpdate={onUpdate} />

          <Separator />

          <Button
            variant="destructive"
            size="sm"
            className="w-full"
            onClick={() => {
              deleteNode(selectedNode.id);
            }}
          >
            Delete Node
          </Button>
        </div>
      </ScrollArea>
    </div>
  );
}
