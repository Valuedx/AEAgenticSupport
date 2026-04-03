from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from security import workflow_access as workflow_access_module
from tools import remediation_tools, status_tools

_sync_spec = importlib.util.spec_from_file_location(
    "sync_user_workflow_access_local",
    Path(__file__).resolve().parents[1] / "scripts" / "sync_user_workflow_access.py",
)
assert _sync_spec and _sync_spec.loader
sync_module = importlib.util.module_from_spec(_sync_spec)
_sync_spec.loader.exec_module(sync_module)


def test_filter_tool_hits_for_user_respects_org_boundaries(monkeypatch):
    monkeypatch.setattr(workflow_access_module, "is_workflow_admin", lambda user_id: False)
    monkeypatch.setattr(
        workflow_access_module,
        "_get_allowed_workflow_pairs",
        lambda user_id, org_code, require_execute: {("wf-1", "ORG-A")},
    )
    monkeypatch.setattr(
        workflow_access_module,
        "_resolve_workflow_identity",
        lambda **kwargs: (
            str(kwargs.get("workflow_id") or "").strip(),
            str(kwargs.get("workflow_name") or "").strip(),
            str(kwargs.get("org_code") or "").strip(),
        ),
    )

    hits = [
        {
            "name": "wf_a_org_a",
            "metadata": {
                "source": "automationedge",
                "workflow_id": "wf-1",
                "workflow_name": "WF_A",
                "org_code": "ORG-A",
            },
        },
        {
            "name": "wf_a_org_b",
            "metadata": {
                "source": "automationedge",
                "workflow_id": "wf-1",
                "workflow_name": "WF_A",
                "org_code": "ORG-B",
            },
        },
        {
            "name": "search_knowledge_base",
            "metadata": {"source": "static"},
        },
    ]

    filtered = workflow_access_module.filter_tool_hits_for_user("user-1", "ORG-A", hits)

    assert [item["name"] for item in filtered] == ["wf_a_org_a", "search_knowledge_base"]


def test_can_execute_workflow_admin_bypass_relies_on_db_flag(monkeypatch):
    monkeypatch.setitem(workflow_access_module.CONFIG, "WF_ACCESS_EXECUTE_AUTH_MODE", "user_admin")
    monkeypatch.setattr(workflow_access_module, "_get_allowed_workflow_pairs", lambda *args, **kwargs: set())

    monkeypatch.setattr(workflow_access_module, "is_workflow_admin", lambda user_id: True)
    assert workflow_access_module.can_execute_workflow("user-1", "wf-1", "ORG-A") is True

    monkeypatch.setattr(workflow_access_module, "is_workflow_admin", lambda user_id: False)
    assert workflow_access_module.can_execute_workflow("user-1", "wf-1", "ORG-A") is False


def test_can_execute_workflow_allows_service_account_mode(monkeypatch):
    monkeypatch.setitem(workflow_access_module.CONFIG, "WF_ACCESS_EXECUTE_AUTH_MODE", "service_account")
    monkeypatch.setitem(workflow_access_module.CONFIG, "AE_USERNAME", "Srushti V")
    monkeypatch.setitem(workflow_access_module.CONFIG, "AE_PASSWORD", "Pune@123")
    monkeypatch.setattr(workflow_access_module, "is_workflow_admin", lambda user_id: False)

    assert workflow_access_module.can_execute_workflow("user-1", "wf-1", "ORG-A") is True


def test_trigger_workflow_denies_unauthorized_user(monkeypatch):
    class StubClient:
        def resolve_cached_workflow_name(self, workflow_name, **kwargs):
            return "WF_Claims"

        def get_cached_workflow_info(self, workflow_name, **kwargs):
            return ("wf-claims", [])

        def execute_workflow(self, **kwargs):
            raise AssertionError("execute_workflow should not be called for unauthorized users")

    monkeypatch.setattr(remediation_tools, "get_ae_client", lambda: StubClient())
    monkeypatch.setattr(remediation_tools, "can_execute_workflow", lambda user_id, workflow_id, org_code: False)
    monkeypatch.setattr(remediation_tools, "is_execute_enforced", lambda: True)

    result = remediation_tools.trigger_workflow(
        "Claims",
        {},
        user_id="user-1",
        org_code="ORG-A",
    )

    assert result["success"] is False
    assert "not available" in result["error"].lower()


def test_t4_execute_and_poll_denies_unauthorized_user(monkeypatch):
    class StubClient:
        def resolve_cached_workflow_name(self, workflow_name, **kwargs):
            return "WF_Claims"

        def get_cached_workflow_id(self, workflow_name, **kwargs):
            return "wf-claims"

        def execute_workflow(self, **kwargs):
            raise AssertionError("execute_workflow should not be called for unauthorized users")

    monkeypatch.setattr(status_tools, "get_ae_client", lambda: StubClient())
    monkeypatch.setattr(status_tools, "can_execute_workflow", lambda user_id, workflow_id, org_code: False)
    monkeypatch.setattr(status_tools, "is_execute_enforced", lambda: True)

    result = status_tools.t4_execute_and_poll(
        "Claims",
        "wf-claims",
        user_id="user-1",
        org_code="ORG-A",
    )

    assert result["success"] is False
    assert "not authorized" in result["error"].lower()


def test_get_execution_status_filters_to_user_visible_workflows(monkeypatch):
    class StubClient:
        def get_execution_status(self, execution_id):
            return {
                "id": execution_id,
                "workflowName": "WF_Claims",
                "workflowId": "wf-claims",
                "orgCode": "ORG-A",
                "status": "Failure",
            }

    monkeypatch.setattr(status_tools, "get_ae_client", lambda: StubClient())
    monkeypatch.setattr(
        status_tools,
        "_is_visible_workflow_record",
        lambda client, record, user_id, org_code: False,
    )

    result = status_tools.get_execution_status(
        "2506738",
        user_id="user-1",
        org_code="ORG-A",
    )

    assert result["status"] == "UNAUTHORIZED"
    assert "not authorized" in result["message"].lower()


def test_list_workflows_filters_to_authorized_items(monkeypatch):
    class StubClient:
        def list_workflows(self, page_size=100):
            return [
                {"workflowId": "wf-1", "workflowName": "WF_A", "orgCode": "ORG-A", "active": True},
                {"workflowId": "wf-1", "workflowName": "WF_A", "orgCode": "ORG-B", "active": True},
            ]

    monkeypatch.setattr(status_tools, "get_ae_client", lambda: StubClient())
    monkeypatch.setattr(
        status_tools,
        "can_view_workflow",
        lambda user_id, workflow_id, org_code: org_code == "ORG-A",
    )

    result = status_tools.list_workflows(limit=10, user_id="user-1", org_code="ORG-A")

    assert result["count"] == 1
    assert result["workflows"][0]["workflow_id"] == "wf-1"
    assert result["workflows"][0]["workflow_name"] == "WF_A"


class _FakeCursor:
    def __init__(self, rows: list[tuple[str, str]], executed: list[tuple[str, tuple[Any, ...] | None]]):
        self._rows = rows
        self._executed = executed
        self._row = rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None):
        self._executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows: list[tuple[str, str]]):
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []
        self._rows = rows
        self.committed = False

    def cursor(self):
        return _FakeCursor(self._rows, self.executed)

    def commit(self):
        self.committed = True


def test_reconcile_access_rows_removes_stale_grants():
    conn = _FakeConn(rows=[("wf-old", "ORG-A"), ("wf-new", "ORG-A")])

    upserted, removed = sync_module._reconcile_access_rows(
        conn,
        user_id="user-1",
        teams_id="teams-1",
        match_confidence=0.99,
        match_method="username_exact_case",
        access_rows=[
            {
                "workflow_id": "wf-new",
                "org_code": "ORG-A",
                "permission": "execute",
                "ae_user_id": 10,
                "ae_username": "claims.user",
            }
        ],
        dry_run=False,
    )

    assert upserted == 1
    assert removed == 1
    assert conn.committed is True
    assert any("INSERT INTO user_workflow_access" in sql for sql, _ in conn.executed)
    assert any("DELETE FROM user_workflow_access" in sql for sql, _ in conn.executed)


def test_resolve_match_succeeds_for_username_match():
    teams_user = {
        "user_id": "u1",
        "user_name": "Claims.User",
        "user_email": "different.user@company.com",
        "metadata": {},
    }
    ae_users = [{"id": 10, "userName": "claims.user"}]

    result = sync_module._resolve_match(teams_user, ae_users)

    assert result["status"] == "matched"
    assert result["method"] == "username_exact"
    assert isinstance(result["ae_user"], dict)
    assert result["ae_user"].get("id") == 10


def test_resolve_match_falls_back_to_exact_email():
    teams_user = {
        "user_id": "u1",
        "user_name": "Kirtibala Gujar",
        "user_email": "kirtibala.gujar@automationedge.com",
        "metadata": {},
    }
    ae_users = [{"id": 10, "userName": "kgujar", "email": "kirtibala.gujar@automationedge.com"}]

    result = sync_module._resolve_match(teams_user, ae_users)

    assert result["status"] == "matched"
    assert result["method"] == "email_exact"
    assert isinstance(result["ae_user"], dict)
    assert result["ae_user"].get("id") == 10


def test_resolve_match_falls_back_to_email_local_part():
    teams_user = {
        "user_id": "u1",
        "user_name": "Kirtibala Gujar",
        "user_email": "kirtibala.gujar@automationedge.com",
        "metadata": {},
    }
    ae_users = [{"id": 10, "userName": "kirtibala.gujar"}]

    result = sync_module._resolve_match(teams_user, ae_users)

    assert result["status"] == "matched"
    assert result["method"] == "email_local_part"
    assert isinstance(result["ae_user"], dict)
    assert result["ae_user"].get("id") == 10


def test_allowed_workflow_pairs_can_use_ae_username_scope(monkeypatch):
    monkeypatch.setattr(
        workflow_access_module,
        "_get_user_registry_identity",
        lambda user_id: {
            "user_name": "claims.user",
            "user_email": "claims.user@company.com",
            "ae_username": "claims.user",
        },
    )
    monkeypatch.setattr(workflow_access_module, "is_workflow_admin", lambda user_id: False)
    monkeypatch.setattr(
        workflow_access_module,
        "_fetch_allowed_workflow_pairs",
        lambda cur, **kwargs: {("wf-1", "ORG-A")},
    )

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            class _Cursor:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            return _Cursor()

    monkeypatch.setattr(workflow_access_module, "get_conn", lambda: _Conn())

    pairs = workflow_access_module._get_allowed_workflow_pairs(
        "webchat:claims.user",
        "ORG-A",
        require_execute=False,
    )

    assert pairs == {("wf-1", "ORG-A")}


def test_allowed_workflow_pairs_refreshes_when_rows_are_missing(monkeypatch):
    monkeypatch.setattr(
        workflow_access_module,
        "_get_user_registry_identity",
        lambda user_id: {
            "user_name": "claims.user",
            "user_email": "claims.user@company.com",
            "ae_username": "",
        },
    )
    monkeypatch.setattr(workflow_access_module, "is_workflow_admin", lambda user_id: False)

    state = {"calls": 0}

    def _fake_fetch(cur, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return set()
        return {("wf-1", "ORG-A")}

    refreshed: list[tuple[str, dict[str, str] | None]] = []

    monkeypatch.setattr(workflow_access_module, "_fetch_allowed_workflow_pairs", _fake_fetch)
    monkeypatch.setattr(
        workflow_access_module,
        "_refresh_user_access_rows",
        lambda user_id, identity=None: refreshed.append((user_id, identity)) or True,
    )

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            class _Cursor:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            return _Cursor()

    monkeypatch.setattr(workflow_access_module, "get_conn", lambda: _Conn())

    pairs = workflow_access_module._get_allowed_workflow_pairs(
        "webchat:claims.user",
        "ORG-A",
        require_execute=False,
    )

    assert pairs == {("wf-1", "ORG-A")}
    assert refreshed == [
        (
            "webchat:claims.user",
            {
                "user_name": "claims.user",
                "user_email": "claims.user@company.com",
                "ae_username": "",
            },
        )
    ]


def test_refresh_user_access_rows_allows_email_only_identity(monkeypatch):
    workflow_access_module._access_refresh_attempted_at.pop("user-1", None)
    monkeypatch.setitem(workflow_access_module.CONFIG, "WF_ACCESS_ENABLE_TARGETED_SYNC", True)

    calls: list[dict[str, str]] = []

    monkeypatch.setattr(
        workflow_access_module,
        "_load_sync_user_by_id",
        lambda: (lambda **kwargs: calls.append(kwargs) or {"match_found": False, "grants_upserted": 0}),
    )

    refreshed = workflow_access_module._refresh_user_access_rows(
        "user-1",
        {
            "user_name": "",
            "user_email": "claims.user@company.com",
            "ae_username": "",
        },
    )

    assert refreshed is True
    assert calls == [
        {
            "user_id": "user-1",
            "user_name": "",
            "user_email": "claims.user@company.com",
        }
    ]


def test_update_user_registry_link_upserts_when_row_missing():
    conn = _FakeConn(rows=[])

    sync_module._update_user_registry_link(
        conn,
        user_id="webchat:claims.user",
        user_name="claims.user",
        user_email="claims.user@company.com",
        ae_user_id=10,
        ae_username="claims.user",
        ae_is_admin=False,
        dry_run=False,
    )

    assert any("INSERT INTO user_registry" in sql for sql, _ in conn.executed)
    assert any("ON CONFLICT (user_id)" in sql for sql, _ in conn.executed)


def test_catalog_org_for_workflow_falls_back_to_default_org_when_catalog_missing(monkeypatch):
    monkeypatch.setitem(sync_module.CONFIG, "AE_ORG_CODE", "AEGEMS")

    result = sync_module._catalog_org_for_workflow({"all": [], "by_id": {}}, "8942")

    assert result == "AEGEMS"


def test_sync_single_user_persists_ae_link_when_grant_fetch_fails():
    class StubClient:
        def get_user_workflows(self, user_id: str):
            raise RuntimeError("404")

        def get(self, path: str):
            raise RuntimeError("404")

    conn = _FakeConn(rows=[])
    teams_user = {
        "user_id": "webchat:claims.user",
        "user_name": "claims.user",
        "user_email": "claims.user@company.com",
        "metadata": {},
    }
    ae_users = [{"id": 10, "userName": "claims.user"}]
    workflow_catalog = {"all": [], "by_id": {}}

    result = sync_module.sync_single_user(
        teams_user,
        ae_users,
        StubClient(),
        workflow_catalog,
        dry_run=False,
        conn=conn,
    )

    assert result["match_found"] is True
    assert result["ae_user_id"] == 10
    assert result["ae_username"] == "claims.user"
    assert result["grants_upserted"] == 0
    assert any("INSERT INTO user_registry" in sql for sql, _ in conn.executed)
