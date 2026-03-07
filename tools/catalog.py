"""
Catalog models for searchable tools, separated from runtime hydration.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from tools.base import ToolDefinition


@dataclass
class ToolCatalogEntry:
    """Searchable metadata for a tool, independent of runtime handler state."""

    definition: ToolDefinition
    source: str = "static"
    source_ref: str = ""
    hydration_mode: str = "eager"
    latency_class: str = "medium"
    mutating: bool = False
    allowed_agents: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_definition(
        cls,
        definition: ToolDefinition,
        *,
        source: str | None = None,
        source_ref: str = "",
        hydration_mode: str = "eager",
        latency_class: str = "",
        mutating: bool | None = None,
        allowed_agents: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ToolCatalogEntry":
        md = dict(definition.metadata or {})
        extra = dict(metadata or {})
        return cls(
            definition=deepcopy(definition),
            source=str(source or md.get("source", "static")),
            source_ref=source_ref or str(md.get("source_ref", definition.name)),
            hydration_mode=hydration_mode or str(md.get("hydration_mode", "eager")),
            latency_class=latency_class or str(md.get("latency_class", "medium")),
            mutating=bool(definition.tier != "read_only" if mutating is None else mutating),
            allowed_agents=list(allowed_agents or md.get("allowed_agents", [])),
            metadata=extra,
        )

    @property
    def name(self) -> str:
        return self.definition.name

    def to_tool_definition(self) -> ToolDefinition:
        return deepcopy(self.definition)

    def to_rag_document(self) -> dict:
        doc = self.to_tool_definition().to_rag_document()
        metadata = doc.setdefault("metadata", {})
        metadata["source"] = self.source
        metadata["source_ref"] = self.source_ref
        metadata["hydration_mode"] = self.hydration_mode
        metadata["llm_callable"] = self.hydration_mode != "execute_via_generic_runner"
        metadata["latency_class"] = self.latency_class
        metadata["mutating"] = self.mutating
        metadata["allowed_agents"] = list(self.allowed_agents)
        if self.hydration_mode == "execute_via_generic_runner":
            metadata["use_tool"] = str(
                self.metadata.get("use_tool")
                or self.definition.metadata.get("use_tool")
                or "trigger_workflow"
            )
        if self.metadata:
            metadata["catalog"] = dict(self.metadata)
        return doc
