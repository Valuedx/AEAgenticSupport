/**
 * DynamicConfigForm — renders a config panel from a registry JSON schema.
 *
 * Field rendering rules (per schema entry type):
 *  - string + enum      → Select
 *  - string (systemPrompt / approvalMessage / body) → Textarea
 *  - string             → Input text
 *  - number / integer   → Input number (step 0.1 / 1, min/max from schema)
 *  - boolean            → Switch
 *  - array (tools field on react_agent) → ToolMultiSelect (live MCP list)
 *  - array (other)      → Textarea (JSON)
 *  - object             → Textarea (JSON, validated on blur)
 */

import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import type { ToolOut } from "@/lib/api";
import { ExpressionInput } from "@/components/sidebar/ExpressionInput";
import { getExpressionVariables } from "@/lib/expressionVariables";
import { useFlowStore } from "@/store/flowStore";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FieldSchema = {
  type: string;
  default?: unknown;
  enum?: unknown[];
  min?: number;
  max?: number;
  items?: { type: string };
};

export interface DynamicConfigFormProps {
  nodeType: string;                                    // e.g. "react_agent"
  schema: Record<string, FieldSchema>;
  config: Record<string, unknown>;
  onUpdate: (partial: { config: Record<string, unknown> }) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** camelCase → "Camel Case" */
function humanize(key: string): string {
  return key
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (c) => c.toUpperCase())
    .trim();
}

const TEXTAREA_KEYS = new Set(["systemPrompt", "approvalMessage", "body"]);

// Fields that accept safe_eval dot-path expressions (e.g. node_2.intent == "x")
const EXPRESSION_KEYS = new Set(["condition", "arrayExpression", "sessionIdExpression", "userMessageExpression"]);

// Fields that accept a bare node ID (e.g. node_3)
const NODE_ID_KEYS = new Set(["responseNodeId", "historyNodeId"]);

// Fields that accept Jinja2 templates (e.g. {{ trigger.message }})
const JINJA2_KEYS = new Set(["systemPrompt"]);

// ---------------------------------------------------------------------------
// Tool multi-select sub-component (for react_agent tools field)
// ---------------------------------------------------------------------------

function ToolMultiSelect({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (names: string[]) => void;
}) {
  const [tools, setTools] = useState<ToolOut[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listTools().then((ts) => {
      setTools(ts);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-xs text-muted-foreground">Loading tools…</p>;
  }

  if (tools.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No tools available (MCP server may be offline).
        Leave empty to auto-discover all tools at runtime.
      </p>
    );
  }

  const toggleTool = (name: string) => {
    if (selected.includes(name)) {
      onChange(selected.filter((n) => n !== name));
    } else {
      onChange([...selected, name]);
    }
  };

  // Group by category
  const byCategory: Record<string, ToolOut[]> = {};
  for (const t of tools) {
    (byCategory[t.category] ??= []).push(t);
  }

  return (
    <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
      {selected.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          None selected — all tools auto-discovered at runtime
        </p>
      )}
      {Object.entries(byCategory).map(([cat, catTools]) => (
        <div key={cat} className="space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {cat}
          </p>
          {catTools.map((t) => {
            const checked = selected.includes(t.name);
            return (
              <label
                key={t.name}
                className="flex items-start gap-2 cursor-pointer group"
              >
                <input
                  type="checkbox"
                  className="mt-0.5 shrink-0"
                  checked={checked}
                  onChange={() => toggleTool(t.name)}
                />
                <div className="min-w-0">
                  <span className={`text-xs ${checked ? "text-foreground" : "text-muted-foreground"} group-hover:text-foreground transition-colors`}>
                    {t.title || t.name}
                  </span>
                  <Badge
                    variant="outline"
                    className="ml-1.5 text-[9px] px-1 py-0"
                  >
                    {t.safety_tier}
                  </Badge>
                </div>
              </label>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DynamicConfigForm({
  nodeType,
  schema,
  config,
  onUpdate,
}: DynamicConfigFormProps) {
  const [jsonErrors, setJsonErrors] = useState<Record<string, boolean>>({});

  // Expression autocomplete — build variable suggestions from canvas state
  const nodes = useFlowStore((s) => s.nodes);
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const exprSuggestions = getExpressionVariables(nodes, selectedNodeId, "expression");
  const nodeIdSuggestions = getExpressionVariables(nodes, selectedNodeId, "nodeId");
  const jinja2Suggestions = getExpressionVariables(nodes, selectedNodeId, "jinja2");

  const update = (key: string, value: unknown) => {
    onUpdate({ config: { ...config, [key]: value } });
  };

  const handleJsonBlur = (key: string, raw: string) => {
    try {
      update(key, JSON.parse(raw));
      setJsonErrors((e) => ({ ...e, [key]: false }));
    } catch {
      setJsonErrors((e) => ({ ...e, [key]: true }));
    }
  };

  return (
    <div className="space-y-4">
      {Object.entries(schema).map(([key, field]) => {
        const value = config[key];

        // ---- enum → Select ----
        if (field.enum && field.enum.length > 0) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <Select
                value={String(value ?? field.default ?? "")}
                onValueChange={(v) => update(key, v)}
              >
                <SelectTrigger id={key}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {field.enum.map((opt) => (
                    <SelectItem key={String(opt)} value={String(opt)}>
                      {String(opt)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          );
        }

        // ---- tools array on react_agent → ToolMultiSelect ----
        if (field.type === "array" && key === "tools" && nodeType === "react_agent") {
          const selected = Array.isArray(value) ? (value as string[]) : [];
          return (
            <div key={key} className="space-y-2">
              <Label>{humanize(key)}</Label>
              <ToolMultiSelect
                selected={selected}
                onChange={(names) => update(key, names)}
              />
            </div>
          );
        }

        // ---- other array → JSON textarea ----
        if (field.type === "array") {
          const raw = JSON.stringify(value ?? field.default ?? [], null, 2);
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)} (JSON array)</Label>
              <Textarea
                id={key}
                rows={3}
                defaultValue={raw}
                className={jsonErrors[key] ? "border-red-500" : ""}
                onBlur={(e) => handleJsonBlur(key, e.target.value)}
              />
            </div>
          );
        }

        // ---- object → JSON textarea ----
        if (field.type === "object") {
          const raw = JSON.stringify(value ?? field.default ?? {}, null, 2);
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)} (JSON)</Label>
              <Textarea
                id={key}
                rows={3}
                defaultValue={raw}
                className={jsonErrors[key] ? "border-red-500" : ""}
                onBlur={(e) => handleJsonBlur(key, e.target.value)}
              />
              {jsonErrors[key] && (
                <p className="text-[10px] text-red-500">Invalid JSON</p>
              )}
            </div>
          );
        }

        // ---- boolean → checkbox ----
        if (field.type === "boolean") {
          return (
            <div key={key} className="flex items-center gap-2">
              <input
                id={key}
                type="checkbox"
                checked={Boolean(value ?? field.default ?? false)}
                onChange={(e) => update(key, e.target.checked)}
              />
              <Label htmlFor={key}>{humanize(key)}</Label>
            </div>
          );
        }

        // ---- number / integer → Input number ----
        if (field.type === "number" || field.type === "integer") {
          const step = field.type === "integer" ? 1 : 0.1;
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <Input
                id={key}
                type="number"
                step={step}
                min={field.min}
                max={field.max}
                value={String(value ?? field.default ?? 0)}
                onChange={(e) =>
                  update(
                    key,
                    field.type === "integer"
                      ? parseInt(e.target.value, 10)
                      : parseFloat(e.target.value),
                  )
                }
              />
            </div>
          );
        }

        // ---- string (Jinja2 textarea: systemPrompt) ----
        if (field.type === "string" && JINJA2_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <ExpressionInput
                value={String(value ?? field.default ?? "")}
                onChange={(v) => update(key, v)}
                suggestions={jinja2Suggestions}
                multiline
                rows={4}
                placeholder="Use {{ trigger.field }} or {{ node_2.response }}"
              />
            </div>
          );
        }

        // ---- string (plain textarea: approvalMessage, body) ----
        if (field.type === "string" && TEXTAREA_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <Textarea
                id={key}
                rows={4}
                value={String(value ?? field.default ?? "")}
                onChange={(e) => update(key, e.target.value)}
              />
            </div>
          );
        }

        // ---- string (expression field: condition, arrayExpression, *Expression) ----
        if (field.type === "string" && EXPRESSION_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <ExpressionInput
                value={String(value ?? field.default ?? "")}
                onChange={(v) => update(key, v)}
                suggestions={exprSuggestions}
                placeholder="e.g. node_2.intent == &quot;diagnose&quot;"
              />
            </div>
          );
        }

        // ---- string (node ID reference: responseNodeId, historyNodeId) ----
        if (field.type === "string" && NODE_ID_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <ExpressionInput
                value={String(value ?? field.default ?? "")}
                onChange={(v) => update(key, v)}
                suggestions={nodeIdSuggestions}
                placeholder="e.g. node_4"
              />
            </div>
          );
        }

        // ---- string → Input text ----
        return (
          <div key={key} className="space-y-2">
            <Label htmlFor={key}>{humanize(key)}</Label>
            <Input
              id={key}
              type="text"
              value={String(value ?? field.default ?? "")}
              onChange={(e) => update(key, e.target.value)}
            />
          </div>
        );
      })}
    </div>
  );
}
