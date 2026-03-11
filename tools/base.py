"""
Base data structures for tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tools.automationedge_client import (
    AutomationEdgeClient,
    get_automationedge_client,
)


@dataclass
class ToolDefinition:
    """Definition of a single tool available to the agent."""

    name: str
    description: str
    category: str
    tier: str
    parameters: dict = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)
    protected_workflows: list[str] = field(default_factory=list)
    always_available: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    use_when: str = ""
    avoid_when: str = ""
    input_examples: list[dict[str, Any]] = field(default_factory=list)

    def _format_examples(self, limit: int = 2) -> list[str]:
        rendered: list[str] = []
        for example in self.input_examples[:limit]:
            try:
                text = json.dumps(example, ensure_ascii=True, sort_keys=True)
            except TypeError:
                text = str(example)
            if len(text) > 240:
                text = text[:237] + "..."
            rendered.append(text)
        return rendered

    def _build_llm_description(self) -> str:
        parts = [self.description.strip()]
        title = str(self.metadata.get("title", "") or "").strip()
        if title and title.lower() != self.name.lower():
            parts.insert(0, f"{title}.")
        workflow_name = str(self.metadata.get("workflow_name", "") or "").strip()
        if workflow_name:
            parts.append(f"Backed by workflow: {workflow_name}.")
        safety = str(self.metadata.get("safety", "") or "").strip()
        if safety:
            parts.append(f"Safety: {safety}.")
        if self.use_when:
            parts.append(f"Use when: {self.use_when.strip()}")
        if self.avoid_when:
            parts.append(f"Avoid when: {self.avoid_when.strip()}")
        if self.required_params:
            parts.append(
                f"Required parameters: {', '.join(self.required_params)}."
            )
        tags = self.metadata.get("tags", [])
        if isinstance(tags, list) and tags:
            parts.append(f"Tags: {', '.join(str(t) for t in tags[:8])}.")
        examples = self._format_examples(limit=1)
        if examples:
            parts.append(f"Example arguments: {examples[0]}")
        return " ".join(part for part in parts if part)

    def to_rag_document(self) -> dict:
        """Build a maximally rich document for RAG embedding.

        Rules:
        - Include ALL parameters with their type AND description text.
        - Collect tags from every metadata location (metadata.tags, mcp_meta.tags,
          mcp_meta.extra_tags) so synonyms are always embedded.
        - Fall back to mcp_meta for use_when/avoid_when if not set on the definition.
        - Include all input examples.
        """
        meta = self.metadata or {}
        mcp_meta = meta.get("mcp_meta", {}) or {}

        # ── Tags: merge from every possible source ──────────────────────────
        all_tags: list[str] = []
        for tag_src in [
            meta.get("tags", []),
            mcp_meta.get("tags", []),
            mcp_meta.get("extra_tags", []),
        ]:
            if isinstance(tag_src, list):
                all_tags.extend(str(t) for t in tag_src if t)
        seen_tags: set[str] = set()
        unique_tags: list[str] = []
        for t in all_tags:
            if t not in seen_tags:
                seen_tags.add(t)
                unique_tags.append(t)

        # ── use_when / avoid_when: prefer definition, fallback to mcp_meta ──
        use_when = self.use_when or str(mcp_meta.get("use_when", "") or "")
        avoid_when = self.avoid_when or str(mcp_meta.get("avoid_when", "") or "")

        # ── Parameters: full name + type + description + required flag ───────
        param_lines: list[str] = []
        for param_name, param_schema in (self.parameters or {}).items():
            if not isinstance(param_schema, dict):
                continue
            p_type = str(param_schema.get("type", "any"))
            p_desc = str(param_schema.get("description", "") or "").strip()
            p_enum = param_schema.get("enum", [])
            p_required = "required" if param_name in self.required_params else "optional"
            parts = [f"  - {param_name} ({p_type}, {p_required})"]
            if p_desc:
                parts.append(f": {p_desc}")
            if p_enum and isinstance(p_enum, list):
                parts.append(f" [values: {', '.join(str(v) for v in p_enum[:8])}]")
            param_lines.append("".join(parts))

        params_block = "\n".join(param_lines) if param_lines else "  none"

        # ── Examples: all of them ────────────────────────────────────────────
        example_lines: list[str] = []
        for ex in self.input_examples:
            try:
                import json as _json
                text = _json.dumps(ex, ensure_ascii=False, sort_keys=True)
                if len(text) > 300:
                    text = text[:297] + "..."
                example_lines.append(f"  {text}")
            except Exception:
                example_lines.append(f"  {ex}")
        examples_block = "\n".join(example_lines) if example_lines else "  none"

        title = str(meta.get("title", "") or "").strip()

        content = "\n".join([
            f"Tool: {self.name}",
            f"Title: {title or self.name}",
            f"Category: {self.category}",
            f"Tier: {self.tier}",
            f"Safety: {meta.get('safety', '') or 'not specified'}",
            f"Description: {self.description}",
            f"Use when: {use_when or 'not specified'}",
            f"Avoid when: {avoid_when or 'not specified'}",
            f"Tags / Synonyms: {', '.join(unique_tags) if unique_tags else 'none'}",
            f"Required parameters: {', '.join(self.required_params) or 'none'}",
            "Parameters (name, type, required/optional, description):",
            params_block,
            "Input examples:",
            examples_block,
        ])

        return {
            "id": f"tool-{self.name}",
            "content": content,
            "metadata": {
                "tool_name": self.name,
                "title": title,
                "category": self.category,
                "tier": self.tier,
                "source": meta.get("source", "static"),
                "workflow_name": meta.get("workflow_name", ""),
                "always_available": self.always_available,
                "dynamic": bool(meta.get("dynamic", False)),
                "tags": unique_tags,
                "safety": meta.get("safety", ""),
                "use_when": use_when,
                "avoid_when": avoid_when,
                "input_examples": self.input_examples[:2],
                "structured_output": bool(meta.get("structured_output", False)),
            },
        }


    def to_llm_schema(self) -> dict:
        """Return a schema compatible with GenAI / Vertex AI FunctionDeclaration."""
        return {
            "name": self.name,
            "description": self._build_llm_description(),
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            },
        }


@dataclass
class ToolResult:
    """Result from executing a tool."""

    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    tool_name: str = ""


def get_ae_client() -> AutomationEdgeClient:
    """Compatibility wrapper for existing imports in static tool modules."""

    return get_automationedge_client()

