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
        workflow_name = str(self.metadata.get("workflow_name", "") or "").strip()
        if workflow_name:
            parts.append(f"Backed by workflow: {workflow_name}.")
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
        # Build a rich searchable content string that helps RAG surface this tool
        param_desc = ", ".join(
            f"{k} ({v.get('type', 'any')}{'*' if k in self.required_params else ''})"
            for k, v in (self.parameters or {}).items()
        )
        examples = self._format_examples()
        return {
            "id": f"tool-{self.name}",
            "content": (
                f"Tool: {self.name}\n"
                f"Category: {self.category}\n"
                f"Risk tier: {self.tier}\n"
                f"Description: {self.description}\n"
                f"Use when: {self.use_when or 'not specified'}\n"
                f"Avoid when: {self.avoid_when or 'not specified'}\n"
                f"Parameters: {param_desc}\n"
                f"Required: {', '.join(self.required_params) or 'none'}\n"
                f"Tags: {', '.join(self.metadata.get('tags', [])) or 'none'}\n"
                f"Examples: {' | '.join(examples) if examples else 'none'}"
            ),
            "metadata": {
                "tool_name": self.name,
                "category": self.category,
                "tier": self.tier,
                "source": self.metadata.get("source", "static"),
                "workflow_name": self.metadata.get("workflow_name", ""),
                "always_available": self.always_available,
                "dynamic": bool(self.metadata.get("dynamic", False)),
                "tags": self.metadata.get("tags", []),
                "use_when": self.use_when,
                "avoid_when": self.avoid_when,
                "input_examples": self.input_examples[:2],
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

