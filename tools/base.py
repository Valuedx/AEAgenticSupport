"""
Base data structures for tools.
"""
from __future__ import annotations

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

    def to_rag_document(self) -> dict:
        return {
            "id": f"tool-{self.name}",
            "content": (
                f"Tool: {self.name}\n"
                f"Category: {self.category}\n"
                f"Risk tier: {self.tier}\n"
                f"Description: {self.description}\n"
                f"Parameters: {self.parameters}\n"
                f"Required: {self.required_params}"
            ),
            "metadata": {
                "tool_name": self.name,
                "category": self.category,
                "tier": self.tier,
                "source": self.metadata.get("source", "static"),
            },
        }

    def to_llm_schema(self) -> dict:
        """Return a schema compatible with Vertex AI FunctionDeclaration."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            },
        }

    def to_vertex_function_declaration(self):
        """Return a Vertex AI FunctionDeclaration object."""
        from vertexai.generative_models import FunctionDeclaration

        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            },
        )


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

