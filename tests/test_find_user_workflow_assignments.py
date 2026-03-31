from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "find_user_workflow_assignments.py"
_SPEC = spec_from_file_location("find_user_workflow_assignments", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_find_matching_users = _MODULE._find_matching_users
_build_admin_workflow_permissions = _MODULE._build_admin_workflow_permissions
_build_user_workflow_permissions = _MODULE._build_user_workflow_permissions
_parse_permission_blocks = _MODULE._parse_permission_blocks
_resolve_workflow_details = _MODULE._resolve_workflow_details


class _DummyClient:
    def get_workflow(self, workflow_id: str):
        return {
            "id": int(workflow_id),
            "name": f"Workflow {workflow_id}",
            "params": [
                {
                    "name": "runtime_param",
                    "type": "String",
                    "displayName": "Runtime Param",
                    "optional": False,
                    "secret": False,
                }
            ],
        }

    def get_workflow_runtime_params(self, workflow_id: str):
        return []

    def get_user_workflows(self, user_id: str):
        return []


def test_find_matching_users_prioritizes_exact_username_and_full_name():
    users = [
        {"id": 1, "userName": "kce", "firstName": "Knowledge", "lastName": "Edge"},
        {"id": 2, "userName": "knowledge.ops", "firstName": "Knowledge", "lastName": "Ops"},
        {"id": 3, "userName": "edge.team", "firstName": "Edge", "lastName": "Team"},
        {"id": 4, "userName": "ae.monitor", "firstName": "Automation", "lastName": "Monitor"},
    ]

    username_matches = _find_matching_users(users, "kce")
    assert username_matches[0]["id"] == 1

    full_name_matches = _find_matching_users(users, "Knowledge Edge")
    assert full_name_matches[0]["id"] == 1

    id_matches = _find_matching_users(users, "1")
    assert id_matches[0]["id"] == 1

    first_name_only_matches = _find_matching_users(users, "Automation")
    assert first_name_only_matches == []


def test_parse_permission_blocks_maps_workflow_ids_to_names():
    payload = [
        {
            "type": "Workflow",
            "permissions": [
                {"id": 9091, "permission": "rwx", "computedPermission": "rwx"},
                {"id": 9101, "permission": "r", "computedPermission": "r"},
            ],
        },
        {
            "type": "Category",
            "permissions": [
                {"id": 8983, "permission": "r", "computedPermission": "r"},
            ],
        },
    ]

    parsed = _parse_permission_blocks(
        payload,
        workflow_catalog={
            9091: {
                "id": 9091,
                "name": "KB Generator_DB_cleanup",
                "description": "Cleanup workflow",
                "params": [
                    {
                        "name": "incident_id",
                        "type": "String",
                        "display_name": "Incident ID",
                        "required": True,
                        "secret": False,
                        "ui_control_type": "TextBox",
                        "description": "",
                    }
                ],
            }
        },
        category_map={8983: "IT Process Automation"},
        client=_DummyClient(),
    )

    assert parsed["workflow_permissions"] == [
        {
            "id": 9091,
            "name": "KB Generator_DB_cleanup",
            "description": "Cleanup workflow",
            "params": [
                {
                    "name": "incident_id",
                    "type": "String",
                    "display_name": "Incident ID",
                    "required": True,
                    "secret": False,
                    "ui_control_type": "TextBox",
                    "description": "",
                }
            ],
            "param_count": 1,
            "permission": "rwx",
            "computed_permission": "rwx",
        },
        {
            "id": 9101,
            "name": "Workflow 9101",
            "description": "",
            "params": [
                {
                    "name": "runtime_param",
                    "type": "String",
                    "display_name": "Runtime Param",
                    "required": True,
                    "secret": False,
                    "ui_control_type": "",
                    "description": "",
                }
            ],
            "param_count": 1,
            "permission": "r",
            "computed_permission": "r",
        },
    ]
    assert parsed["category_permissions"] == [
        {
            "id": 8983,
            "name": "IT Process Automation",
            "permission": "r",
            "computed_permission": "r",
        }
    ]


def test_resolve_workflow_details_enriches_catalog_entries_with_missing_params():
    client = _DummyClient()
    workflow_catalog = {
        9101: {
            "id": 9101,
            "name": "Workflow 9101",
            "description": "",
            "params": [],
        }
    }

    resolved = _resolve_workflow_details(client, workflow_catalog, 9101)

    assert resolved["name"] == "Workflow 9101"
    assert resolved["params"] == [
        {
            "name": "runtime_param",
            "type": "String",
            "display_name": "Runtime Param",
            "required": True,
            "secret": False,
            "ui_control_type": "",
            "description": "",
        }
    ]


def test_build_admin_workflow_permissions_returns_full_catalog_as_rwx():
    client = _DummyClient()
    workflow_catalog = {
        9091: {
            "id": 9091,
            "name": "KB Generator_DB_cleanup",
            "description": "Cleanup workflow",
            "params": [],
        },
        9101: {
            "id": 9101,
            "name": "Workflow 9101",
            "description": "",
            "params": [],
        },
    }

    workflow_permissions = _build_admin_workflow_permissions(client, workflow_catalog)

    assert workflow_permissions == [
        {
            "id": 9091,
            "name": "KB Generator_DB_cleanup",
            "description": "Cleanup workflow",
            "params": [
                {
                    "name": "runtime_param",
                    "type": "String",
                    "display_name": "Runtime Param",
                    "required": True,
                    "secret": False,
                    "ui_control_type": "",
                    "description": "",
                }
            ],
            "param_count": 1,
            "permission": "rwx",
            "computed_permission": "rwx",
        },
        {
            "id": 9101,
            "name": "Workflow 9101",
            "description": "",
            "params": [
                {
                    "name": "runtime_param",
                    "type": "String",
                    "display_name": "Runtime Param",
                    "required": True,
                    "secret": False,
                    "ui_control_type": "",
                    "description": "",
                }
            ],
            "param_count": 1,
            "permission": "rwx",
            "computed_permission": "rwx",
        },
    ]


def test_build_user_workflow_permissions_limits_to_assigned_workflows():
    class _AssignedWorkflowClient(_DummyClient):
        def get_user_workflows(self, user_id: str):
            assert user_id == "101"
            return [
                {"workflowId": 9101},
            ]

    client = _AssignedWorkflowClient()
    workflow_catalog = {
        9091: {
            "id": 9091,
            "name": "KB Generator_DB_cleanup",
            "description": "Cleanup workflow",
            "params": [],
        },
        9101: {
            "id": 9101,
            "name": "Workflow 9101",
            "description": "",
            "params": [],
        },
    }

    workflow_permissions = _build_user_workflow_permissions(
        client,
        workflow_catalog,
        101,
        fallback_permissions=[],
    )

    assert workflow_permissions == [
        {
            "id": 9101,
            "name": "Workflow 9101",
            "description": "",
            "params": [
                {
                    "name": "runtime_param",
                    "type": "String",
                    "display_name": "Runtime Param",
                    "required": True,
                    "secret": False,
                    "ui_control_type": "",
                    "description": "",
                }
            ],
            "param_count": 1,
            "permission": "assigned",
            "computed_permission": "assigned",
        }
    ]
