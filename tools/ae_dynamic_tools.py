"""
Utilities for mapping AutomationEdge workflow metadata into dynamic tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
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
                "params",
                "workflowparameters",
            }:
                dict_items = [it for it in value if isinstance(it, dict)]
                if dict_items:
                    bag.append(dict_items)
            _collect_parameter_lists(value, bag)
    elif isinstance(payload, list):
        for item in payload:
            _collect_parameter_lists(item, bag)


def _normalize_parameter_list(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return []
    named_items = [
        item
        for item in dict_items
        if str(_get_first_value(item, ("name", "parametername", "paramname", "key"), "") or "").strip()
    ]
    return named_items or dict_items


def _extract_direct_parameter_list(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    for key in (
        "parameters",
        "params",
        "inputParameters",
        "runtimeParameters",
        "workflowParameters",
        "configurationParameters",
    ):
        for actual_key, value in payload.items():
            if _norm_key(actual_key) != _norm_key(key):
                continue
            params = _normalize_parameter_list(value)
            if params:
                return params
    return []


def _select_parameter_list(
    workflow_summary: dict,
    workflow_details: dict,
    cfg: dict,
    merged: dict,
) -> list[dict]:
    for source in (workflow_details, workflow_summary, cfg):
        params = _extract_direct_parameter_list(source)
        if params:
            return params

    parameter_lists: list[list[dict]] = []
    _collect_parameter_lists(merged, parameter_lists)
    return parameter_lists[0] if parameter_lists else []


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


def _coerce_examples(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    examples: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            examples.append(item)
            continue
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                examples.append(parsed)
    return examples[:3]


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
    workflow_id: str
    org_code: str
    description: str
    category: str
    tier: str
    active: bool
    tags: list[str]
    use_when: str
    avoid_when: str
    input_examples: list[dict[str, Any]]
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
                "workflow_id": self.workflow_id,
                "org_code": self.org_code,
                "active": self.active,
                "tags": self.tags,
                "parameter_meta": self.parameter_meta,
            },
            use_when=self.use_when,
            avoid_when=self.avoid_when,
            input_examples=self.input_examples,
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

    workflow_id = str(
        workflow_summary.get("workflowId")
        or workflow_summary.get("id")
        or details.get("workflowId")
        or details.get("id")
        or ""
    ).strip()
    org_code = str(
        workflow_summary.get("orgCode")
        or workflow_summary.get("org_code")
        or details.get("orgCode")
        or details.get("org_code")
        or ""
    ).strip()

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
    use_when = str(
        _get_first_value(
            cfg,
            ("usewhen", "when_to_use", "recommendedfor", "intendeduse"),
            "",
        )
        or ""
    ).strip()
    avoid_when = str(
        _get_first_value(
            cfg,
            ("avoidwhen", "when_not_to_use", "dontusefor", "notfor"),
            "",
        )
        or ""
    ).strip()
    
    # Force avoid_when for status queries to steer toward check_workflow_status
    status_crit = "the user is only asking for the status, history, or last run of this bot (not explicitly asking to run/trigger it now)."
    if avoid_when:
        avoid_when = f"{avoid_when} Also avoid if {status_crit}"
    else:
        avoid_when = f"Avoid if {status_crit}"
    input_examples = _coerce_examples(
        _get_first_value(
            cfg,
            ("inputexamples", "exampleinputs", "examples", "sampleinputs"),
            [],
        )
    )

    params_raw = _select_parameter_list(workflow_summary, details, cfg, merged)

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
                ("displayname", "description", "helptext"),
                f"Parameter '{name}'",
            )
            or f"Parameter '{name}'"
        )
        opt = param.get("optional")
        is_explicitly_optional = (
            opt is True or 
            (isinstance(opt, str) and str(opt).strip().lower() in {"true", "1", "yes", "y"}) or
            param.get("is_optional") is True or
            param.get("required") is False or
            param.get("is_required") is False
        )
        required_flag = not is_explicitly_optional

        extension = _get_first_value(param, ("extension", "fileextension"), None)

        prop_schema = {"type": json_type, "description": description_txt}
        if extension:
            prop_schema["extension"] = str(extension).strip()

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
                "extension": extension,
            }
        )

    return DynamicToolMapping(
        tool_name=tool_name,
        workflow_name=workflow_name,
        workflow_id=workflow_id,
        org_code=org_code,
        description=description,
        category=category,
        tier=_infer_tier(default_tier, cfg),
        active=active,
        tags=tags,
        use_when=use_when,
        avoid_when=avoid_when,
        input_examples=input_examples,
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

