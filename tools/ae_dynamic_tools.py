"""
Utilities for mapping AutomationEdge workflow metadata into dynamic tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional

from tools.base import ToolDefinition

logger = logging.getLogger("ops_agent.tools.ae_dynamic")


def _norm_key(key: str) -> str:
    return "".join(ch.lower() for ch in str(key) if ch.isalnum())


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def _find_dict_by_normalized_key(payload: Any, normalized_key: str) -> Optional[dict]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _norm_key(key) == normalized_key and isinstance(value, dict):
                return value
            found = _find_dict_by_normalized_key(value, normalized_key)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_dict_by_normalized_key(item, normalized_key)
            if found:
                return found
    return None


def _collect_parameter_lists(payload: Any, bag: list[list[dict]]):
    if isinstance(payload, dict):
        for key, value in payload.items():
            norm = _norm_key(key)
            if isinstance(value, list) and norm in {
                "configurationparameters",
                "runtimeparameters",
                "inputparameters",
                "parameters",
                "workflowparameters",
            }:
                dict_items = [it for it in value if isinstance(it, dict)]
                if dict_items:
                    bag.append(dict_items)
            _collect_parameter_lists(value, bag)
    elif isinstance(payload, list):
        for item in payload:
            _collect_parameter_lists(item, bag)


def _get_first_value(d: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    lookup = {_norm_key(k): v for k, v in d.items()}
    for key in keys:
        if key in lookup:
            return lookup[key]
    return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "required", "mandatory"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _ae_type_to_json_type(ae_type: str) -> str:
    norm = _norm_key(ae_type)
    mapping = {
        "string": "string",
        "text": "string",
        "number": "number",
        "integer": "integer",
        "long": "integer",
        "double": "number",
        "float": "number",
        "boolean": "boolean",
        "bool": "boolean",
        "list": "array",
        "array": "array",
        "object": "object",
        "json": "object",
        "file": "string",
        "credential": "string",
    }
    return mapping.get(norm, "string")


def _infer_tier(default_tier: str, config_block: dict) -> str:
    tier = str(
        _get_first_value(
            config_block,
            ("tier", "risktier", "risklevel"),
            default_tier,
        )
        or default_tier
    ).strip().lower()
    allowed = {"read_only", "low_risk", "medium_risk", "high_risk"}
    if tier in allowed:
        return tier
    # map a few common aliases
    if tier in {"readonly", "read"}:
        return "read_only"
    if tier in {"low", "safe"}:
        return "low_risk"
    if tier in {"medium", "moderate"}:
        return "medium_risk"
    if tier in {"high", "dangerous"}:
        return "high_risk"
    return default_tier


@dataclass
class DynamicToolMapping:
    tool_name: str
    workflow_name: str
    description: str
    category: str
    tier: str
    active: bool
    tags: list[str]
    parameters: dict
    required_params: list[str]
    parameter_meta: list[dict]

    def to_tool_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.tool_name,
            description=self.description,
            category=self.category,
            tier=self.tier,
            parameters=self.parameters,
            required_params=self.required_params,
            metadata={
                "source": "automationedge",
                "dynamic": True,
                "workflow_name": self.workflow_name,
                "active": self.active,
                "tags": self.tags,
                "parameter_meta": self.parameter_meta,
            },
        )


def extract_dynamic_tool_mapping(
    workflow_summary: dict,
    workflow_details: Optional[dict] = None,
    default_tier: str = "medium_risk",
) -> Optional[DynamicToolMapping]:
    """
    Convert workflow metadata into a DynamicToolMapping.

    Returns None when no valid Agentic Tool configuration exists.
    """
    details = workflow_details or {}
    merged = {"summary": workflow_summary or {}, "details": details}

    cfg = (
        _find_dict_by_normalized_key(merged, "agenticaitoolconfiguration")
        or _find_dict_by_normalized_key(merged, "agentictoolconfiguration")
        or _find_dict_by_normalized_key(merged, "tooldetails")
    )
    if not cfg:
        return None

    tool_name = str(
        _get_first_value(
            cfg,
            ("toolname", "name"),
            "",
        )
        or ""
    ).strip()
    if not tool_name or " " in tool_name:
        return None

    active = _to_bool(
        _get_first_value(cfg, ("active", "enabled"), True),
        default=True,
    )
    status = str(_get_first_value(cfg, ("status",), "active")).strip().lower()
    if status and status not in {"active", "enabled"}:
        active = False

    workflow_name = str(
        workflow_summary.get("workflowName")
        or workflow_summary.get("name")
        or details.get("workflowName")
        or details.get("name")
        or ""
    ).strip()
    if not workflow_name:
        workflow_name = tool_name

    category = str(
        _get_first_value(cfg, ("category", "group"), "automationedge")
    ).strip() or "automationedge"
    description = str(
        _get_first_value(
            cfg,
            ("tooldescription", "description"),
            f"Execute AutomationEdge workflow '{workflow_name}'",
        )
    ).strip()

    tags_raw = _get_first_value(cfg, ("tags", "labels"), [])
    tags: list[str] = []
    if isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    elif isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    parameter_lists: list[list[dict]] = []
    _collect_parameter_lists(merged, parameter_lists)
    params_raw = max(parameter_lists, key=len) if parameter_lists else []

    properties: dict[str, dict] = {}
    required: list[str] = []
    meta: list[dict] = []

    for param in params_raw:
        name = str(
            _get_first_value(
                param,
                ("name", "parametername", "paramname", "key"),
                "",
            )
            or ""
        ).strip()
        if not name:
            continue
        ae_type = str(
            _get_first_value(
                param,
                ("type", "datatype", "valuetype", "paramtype"),
                "String",
            )
            or "String"
        )
        json_type = _ae_type_to_json_type(ae_type)
        description_txt = str(
            _get_first_value(
                param,
                ("description", "helptext"),
                f"Parameter '{name}'",
            )
            or f"Parameter '{name}'"
        )
        required_flag = _to_bool(
            _get_first_value(param, ("required", "mandatory", "isrequired"), False)
        )

        prop_schema = {"type": json_type, "description": description_txt}
        default_val = _get_first_value(param, ("defaultvalue", "default"), None)
        if default_val is not None:
            prop_schema["default"] = default_val

        properties[name] = prop_schema
        if required_flag and name not in required:
            required.append(name)
        meta.append(
            {
                "name": name,
                "ae_type": ae_type,
                "json_type": json_type,
                "required": required_flag,
                "description": description_txt,
                "default": default_val,
            }
        )

    return DynamicToolMapping(
        tool_name=tool_name,
        workflow_name=workflow_name,
        description=description,
        category=category,
        tier=_infer_tier(default_tier, cfg),
        active=active,
        tags=tags,
        parameters=properties,
        required_params=required,
        parameter_meta=meta,
    )


def extract_dynamic_tool_mappings_from_payload(
    workflows: list[dict],
    *,
    details_by_workflow: Optional[dict[str, dict]] = None,
    default_tier: str = "medium_risk",
) -> list[DynamicToolMapping]:
    details_lookup = details_by_workflow or {}
    mappings: list[DynamicToolMapping] = []
    for wf in _safe_list(workflows):
        if not isinstance(wf, dict):
            continue
        wf_name = str(wf.get("workflowName") or wf.get("name") or "").strip()
        details = details_lookup.get(wf_name, {})
        mapping = extract_dynamic_tool_mapping(
            wf,
            workflow_details=details,
            default_tier=default_tier,
        )
        if mapping:
            mappings.append(mapping)
    return mappings

