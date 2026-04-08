"""
Unit tests for the AutomationEdge REST client.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CONFIG
from tools.automationedge_client import AutomationEdgeClient


class TestAutomationEdgeClient(unittest.TestCase):
    def setUp(self):
        self._backup = dict(CONFIG)
        CONFIG["AE_BASE_URL"] = "https://ae.local"
        CONFIG["AE_REST_BASE_PATH"] = "/aeengine/rest"
        CONFIG["AE_AUTH_ENDPOINT"] = "/authenticate"
        CONFIG["AE_EXECUTE_ENDPOINT"] = "/execute"
        CONFIG["AE_WORKFLOWS_ENDPOINT"] = "/workflows"
        CONFIG["AE_WORKFLOW_DETAILS_ENDPOINT"] = "/workflows/{workflow_identifier}"
        CONFIG["AE_SESSION_HEADER"] = "X-session-token"
        CONFIG["AE_TOKEN_FIELD"] = "token"
        CONFIG["AE_TOKEN_TTL_SECONDS"] = 1800
        CONFIG["AE_USERNAME"] = "user1"
        CONFIG["AE_PASSWORD"] = "pass1"
        CONFIG["AE_API_KEY"] = ""
        CONFIG["AE_ORG_CODE"] = "ORG1"
        CONFIG["AE_DEFAULT_USERID"] = "ops_user"
        CONFIG["AE_TIMEOUT_SECONDS"] = 10
        CONFIG["WF_ACCESS_EXECUTE_AUTH_MODE"] = "service_account"
        self._runtime_value_patch = patch(
            "tools.automationedge_client.get_runtime_value",
            side_effect=lambda key, default=None: CONFIG.get(key, default),
        )
        self._runtime_value_patch.start()

    def tearDown(self):
        self._runtime_value_patch.stop()
        CONFIG.clear()
        CONFIG.update(self._backup)

    def _client_with_transport(self, handler):
        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(
            base_url=CONFIG["AE_BASE_URL"],
            transport=transport,
            verify=False,
            timeout=5,
        )
        return AutomationEdgeClient(client=http_client)

    def test_authenticate_caches_session_token(self):
        calls = {"auth": 0}

        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                calls["auth"] += 1
                return httpx.Response(200, json={"token": "abc-123"})
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        token = client.authenticate()
        self.assertEqual(token, "abc-123")
        self.assertEqual(calls["auth"], 1)

        # second call should use cache
        token2 = client.authenticate()
        self.assertEqual(token2, "abc-123")
        self.assertEqual(calls["auth"], 1)
        client.close()

    def test_authenticate_uses_rest_prefix_query_params_and_session_token_fallback(self):
        seen = {}

        def handler(request: httpx.Request):
            if request.url.path == "/aeengine/rest/authenticate":
                seen["method"] = request.method
                seen["path"] = request.url.path
                seen["username"] = request.url.params.get("username")
                seen["password"] = request.url.params.get("password")
                seen["ep"] = request.url.params.get("ep")
                return httpx.Response(200, json={"sessionToken": "sess-123"})
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        token = client.authenticate()

        self.assertEqual(token, "sess-123")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/aeengine/rest/authenticate")
        self.assertEqual(seen["username"], "user1")
        self.assertEqual(seen["password"], "pass1")
        self.assertEqual(seen["ep"], "true")
        client.close()

    def test_authenticate_retries_other_request_shapes_after_server_error(self):
        calls = []

        def handler(request: httpx.Request):
            if request.url.path != "/aeengine/rest/authenticate":
                return httpx.Response(404, json={})

            calls.append(dict(request.url.params))
            if request.url.params.get("ep") == "true":
                return httpx.Response(500, json={"message": "decrypt failed"})
            return httpx.Response(200, json={"sessionToken": "sess-456"})

        client = self._client_with_transport(handler)
        token = client.authenticate()

        self.assertEqual(token, "sess-456")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].get("ep"), "true")
        self.assertIsNone(calls[1].get("ep"))
        client.close()

    def test_resolve_cached_workflow_name_accepts_numeric_workflow_id(self):
        class _Cursor:
            def __init__(self):
                self._rows = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql, params):
                normalized = " ".join(str(sql).split())
                if "WHERE workflow_id = %s" in normalized:
                    self._rows = [("1439", "TEBT_Workflow", "ORG1")]
                else:
                    self._rows = []

            def fetchall(self):
                return list(self._rows)

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return _Cursor()

        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))
        with patch("config.db.get_conn", return_value=_Conn()):
            resolved = client.resolve_cached_workflow_name("1439")

        self.assertEqual(resolved, "TEBT_Workflow")
        client.close()

    def test_authorized_request_retries_once_on_401(self):
        calls = {"auth": 0, "workflows": 0}

        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                calls["auth"] += 1
                return httpx.Response(200, json={"token": f"token-{calls['auth']}"})
            if request.url.path.endswith("/workflows"):
                calls["workflows"] += 1
                if calls["workflows"] == 1:
                    return httpx.Response(401, json={"error": "expired"})
                return httpx.Response(200, json={"workflows": [{"name": "WF1"}]})
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        workflows = client.list_workflows()
        self.assertEqual(len(workflows), 1)
        self.assertEqual(workflows[0]["name"], "WF1")
        self.assertEqual(calls["workflows"], 2)
        self.assertEqual(calls["auth"], 2)
        client.close()

    def test_authorized_request_handles_429_backoff(self):
        calls = []

        def handler(request: httpx.Request):
            path = request.url.path
            calls.append(path)
            
            if path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            
            if len([p for p in calls if "/test" in p]) == 1:
                return httpx.Response(429, json={"error": "Rate limit exceeded"})
            return httpx.Response(200, json={"status": "success"})

        client = self._client_with_transport(handler)
        
        with patch("time.sleep") as mock_sleep:
            # Use _authorized_request directly to test backoff
            result = client._authorized_request("GET", "/test")
            self.assertEqual(result["status"], "success")
            # Should have called /authenticate once and /test twice
            test_calls = [p for p in calls if "/test" in p]
            self.assertEqual(len(test_calls), 2)
            mock_sleep.assert_called_once_with(5)
        client.close()

    def test_authorized_request_parses_429_retry_after(self):
        calls = []

        def handler(request: httpx.Request):
            path = request.url.path
            calls.append(path)
            
            if path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            
            if len([p for p in calls if "/test-429" in p]) == 1:
                return httpx.Response(
                    429, 
                    json={
                        "message": "You have crossed the service consumption limit configured for your user. Please try after 18 seconds.", 
                        "success": False
                    }
                )
            return httpx.Response(200, json={"status": "success"})

        client = self._client_with_transport(handler)
        
        with patch("time.sleep") as mock_sleep:
            result = client._authorized_request("GET", "/test-429")
            self.assertEqual(result["status"], "success")
            # Should have called /test-429 twice (fail then retry)
            test_calls = [p for p in calls if "/test-429" in p]
            self.assertEqual(len(test_calls), 2)
            # Should have slept for 18 seconds exactly
            mock_sleep.assert_called_once_with(18)
        client.close()

    def test_execute_workflow_payload_contract(self):
        captured = {"payload": None, "header": ""}

        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/execute"):
                captured["header"] = request.headers.get("X-session-token", "")
                captured["payload"] = json.loads(request.content.decode("utf-8"))
                return httpx.Response(
                    200,
                    json={"status": "QUEUED", "requestId": "REQ-101"},
                )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        out = client.execute_workflow(
            workflow_name="File_Write_Workflow",
            params={"count": 2, "dryRun": True, "path": "/tmp/file.txt"},
        )
        self.assertEqual(out.get("requestId"), "REQ-101")
        self.assertEqual(captured["header"], "tok-1")
        self.assertEqual(captured["payload"]["orgCode"], "ORG1")
        self.assertEqual(captured["payload"]["workflowName"], "File_Write_Workflow")
        self.assertEqual(captured["payload"]["userId"], "ops_user")

        params = captured["payload"]["params"]
        typed = {p["name"]: p["type"] for p in params}
        self.assertEqual(typed["count"], "Number")
        self.assertEqual(typed["dryRun"], "Boolean")
        self.assertEqual(typed["path"], "String")
        client.close()

    def test_execute_workflow_uses_service_account_user_id_even_when_chat_user_is_passed(self):
        captured = {"payload": None}

        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/execute"):
                captured["payload"] = json.loads(request.content.decode("utf-8"))
                return httpx.Response(
                    200,
                    json={"status": "QUEUED", "requestId": "REQ-202"},
                )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        out = client.execute_workflow(
            workflow_name="WF_Updated3_Diskcleanup",
            workflow_id="8942",
            user_id="webchat:kirtibala.gujar@valuedx.com",
        )

        self.assertEqual(out.get("requestId"), "REQ-202")
        self.assertEqual(captured["payload"]["userId"], "ops_user")
        client.close()

    def test_list_workflows_falls_back_to_post_when_get_fails(self):
        calls = {"get": 0, "post": 0}

        def handler(request: httpx.Request):
            path = request.url.path
            if path.endswith("/authenticate"):
                return httpx.Response(200, json={"sessionToken": "sess-1"})
            if path.endswith("/workflows"):
                if request.method == "GET":
                    calls["get"] += 1
                    return httpx.Response(500, json={"errorCode": "AE-1002"})
                if request.method == "POST":
                    calls["post"] += 1
                    return httpx.Response(
                        200,
                        json={"data": [{"id": 1, "name": "WF-POST"}]},
                    )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        workflows = client.list_workflows()
        self.assertEqual(len(workflows), 1)
        self.assertEqual(workflows[0]["name"], "WF-POST")
        self.assertEqual(calls["get"], 1)
        self.assertEqual(calls["post"], 1)
        client.close()

    def test_resolve_cached_workflow_name_falls_back_to_live_workflow_list_when_cache_misses(self):
        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))

        with patch("config.db.get_conn", side_effect=RuntimeError("db unavailable")), \
             patch.object(
                 client,
                 "list_workflows",
                 return_value=[
                     {
                         "id": "1440",
                         "name": "Cashier Receipting Report Download -MG22P1W25",
                         "orgCode": "ORG1",
                     }
                 ],
             ), \
             patch.object(client, "sync_workflow_catalog", return_value=1) as mock_sync:
            resolved = client.resolve_cached_workflow_name(
                "Cashier Receipting Report Download -MG22P1W25"
            )

        self.assertEqual(resolved, "Cashier Receipting Report Download -MG22P1W25")
        mock_sync.assert_called_once()
        client.close()

    def test_get_cached_workflow_info_falls_back_to_live_workflow_details_when_cache_misses(self):
        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))

        with patch("config.db.get_conn", side_effect=RuntimeError("db unavailable")), \
             patch.object(
                 client,
                 "list_workflows",
                 return_value=[
                     {
                         "id": "1440",
                         "name": "Cashier Receipting Report Download -MG22P1W25",
                         "orgCode": "ORG1",
                     }
                 ],
             ), \
             patch.object(
                 client,
                 "get_workflow_details",
                 return_value={
                     "id": "1440",
                     "name": "Cashier Receipting Report Download -MG22P1W25",
                     "configurationParameters": [
                         {"name": "batch_id", "type": "String", "optional": False}
                     ],
                 },
             ), \
             patch.object(client, "sync_workflow_catalog", return_value=1):
            workflow_id, params = client.get_cached_workflow_info(
                "Cashier Receipting Report Download -MG22P1W25"
            )

        self.assertEqual(workflow_id, "1440")
        self.assertEqual(params, [{"name": "batch_id", "type": "String", "optional": False}])
        client.close()

    def test_get_workflow_latest_instance_uses_modern_status_endpoint(self):
        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/api/v1/workflows/Policy_Renewal_Batch/instances"):
                return httpx.Response(
                    200,
                    json={
                        "instances": [{
                            "workflow_name": "Policy_Renewal_Batch",
                            "status": "active",
                            "errorMessage": "Input file missing",
                        }]
                    },
                )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        client.resolve_cached_workflow_name = lambda _: ""

        result = client.get_workflow_latest_instance("Policy_Renewal_Batch")

        self.assertEqual(result["workflow_name"], "Policy_Renewal_Batch")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["errorMessage"], "Input file missing")
        client.close()

    def test_get_workflow_instances_uses_modern_executions_endpoint(self):
        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/api/v1/workflows/Policy_Renewal_Batch/executions"):
                return httpx.Response(
                    200,
                    json={
                        "executions": [
                            {"execution_id": "EX-0042", "status": "failed"},
                            {"execution_id": "EX-0043", "status": "success"},
                        ]
                    },
                )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        client.resolve_cached_workflow_name = lambda _: ""

        result = client.get_workflow_instances("Policy_Renewal_Batch", limit=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["execution_id"], "EX-0042")
        client.close()

    def test_check_agent_status_uses_modern_agents_endpoint_without_org(self):
        CONFIG["AE_ORG_CODE"] = ""

        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/api/v1/agents/status"):
                return httpx.Response(
                    200,
                    json={
                        "agents": [
                            {"name": "agent-prod-01", "status": "online", "id": "A1"},
                            {"name": "agent-prod-02", "status": "offline", "id": "A2"},
                        ]
                    },
                )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)

        result = client.check_agent_status()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["agentName"], "agent-prod-01")
        self.assertEqual(result[0]["agentState"], "ONLINE")
        self.assertEqual(result[0]["agentId"], "A1")
        client.close()

    def test_get_execution_logs_uses_modern_logs_endpoint(self):
        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/api/v1/executions/EX-0042/logs"):
                return httpx.Response(
                    200,
                    json={
                        "execution_id": "EX-0042",
                        "logs": [{"level": "ERROR", "message": "File missing"}],
                    },
                )
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)

        result = client.get_execution_logs("EX-0042", tail=10)

        self.assertEqual(result["execution_id"], "EX-0042")
        self.assertEqual(result["logs"][0]["level"], "ERROR")
        client.close()

    def test_get_execution_logs_t4_fallback_flow(self):
        calls = []

        def handler(request: httpx.Request):
            path = request.url.path
            calls.append((request.method, path))
            
            if path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            
            # Phase 1: Fail direct paths with 400 (unsupported) or 429 (rate limit)
            if "/logs" in path and not ("debuglogs" in path or "download" in path):
                return httpx.Response(400, json={"error": "Not supported on T4 directly"})
            
            # Implementation calls get_execution_status first in Phase 2
            if path.endswith("/workflowinstances/2506738"):
                return httpx.Response(200, json={
                    "status": "Complete",
                    "startTime": 1000,
                    "endTime": 2000
                })
            
            # Step 2: Request debug logs (POST /agent/debuglogs)
            if path.endswith("/agent/debuglogs") and request.method == "POST":
                return httpx.Response(200, json={"id": 1248})
            
            # Step 3: Poll status (GET /agent/debuglogs/{id})
            if path.endswith("/agent/debuglogs/1248"):
                poll_count = sum(1 for c in calls if c[1].endswith("/agent/debuglogs/1248"))
                if poll_count < 2:
                    return httpx.Response(200, json={"id": 1248, "logFileLink": None, "status": "IN_PROGRESS"})
                return httpx.Response(200, json={"id": 1248, "logFileLink": "/download/1248.zip", "status": "COMPLETED"})
            
            # Step 4: Download (GET /download/1248.zip)
            if path.endswith("/download/1248.zip"):
                return httpx.Response(200, content=b"ZIP_DATA", headers={"Content-Type": "application/zip"})
                
            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)
        
        with patch("time.sleep"): # Speed up the test
            result = client.get_execution_logs("2506738")
            
        self.assertTrue(result.get("is_zip"))
        self.assertEqual(result.get("log_zip_content"), b"ZIP_DATA")
        
        # Verify flow sequence
        method_paths = [c[1] for c in calls]
        self.assertTrue(any(p.endswith("/agent/debuglogs") for p in method_paths))
        self.assertTrue(any(p.endswith("/download/1248.zip") for p in method_paths))
        client.close()

    def test_get_execution_logs_recovers_from_ae1603_via_list_poll(self):
        calls = []

        def handler(request: httpx.Request):
            path = request.url.path
            calls.append((request.method, path))

            if path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})

            if "/logs" in path and not ("debuglogs" in path or "download" in path):
                return httpx.Response(400, json={"error": "Not supported on T4 directly"})

            if path.endswith("/workflowinstances/2585726"):
                return httpx.Response(
                    200,
                    json={"status": "Complete", "startTime": 1000, "endTime": 2000},
                )

            if path.endswith("/agent/debuglogs") and request.method == "GET":
                list_count = sum(
                    1
                    for method, seen_path in calls
                    if method == "GET" and seen_path.endswith("/agent/debuglogs")
                )
                if list_count < 2:
                    return httpx.Response(200, json={"data": []})
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 1537,
                                "workflowInstanceId": 2585726,
                                "status": "COMPLETE",
                                "logFileLink": "/download/1537.zip",
                            }
                        ]
                    },
                )

            if path.endswith("/agent/debuglogs") and request.method == "POST":
                return httpx.Response(200, json={"id": 1537})

            if path.endswith("/agent/debuglogs/1537"):
                return httpx.Response(
                    500,
                    json={"message": "Invalid log request id", "errorCode": "AE-1603", "success": False},
                )

            if path.endswith("/download/1537.zip"):
                return httpx.Response(200, content=b"ZIP_DATA", headers={"Content-Type": "application/zip"})

            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)

        with patch("time.sleep"):
            result = client.get_execution_logs("2585726")

        self.assertTrue(result.get("is_zip"))
        self.assertEqual(result.get("log_zip_content"), b"ZIP_DATA")
        self.assertTrue(any(path.endswith("/agent/debuglogs/1537") for _, path in calls))
        self.assertTrue(any(path.endswith("/agent/debuglogs") for _, path in calls))
        self.assertTrue(any(path.endswith("/download/1537.zip") for _, path in calls))
        client.close()

    def test_get_execution_logs_handles_direct_zip_poll_response(self):
        calls = []

        def handler(request: httpx.Request):
            path = request.url.path
            calls.append((request.method, path))

            if path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})

            if "/logs" in path and "debuglogs" not in path:
                return httpx.Response(400, json={"error": "Not supported on T4 directly"})

            if path.endswith("/workflowinstances/22853"):
                return httpx.Response(
                    200,
                    json={"status": "Complete", "startTime": 1000, "endTime": 2000},
                )

            if path.endswith("/agent/debuglogs") and request.method == "GET":
                return httpx.Response(200, json={"data": []})

            if path.endswith("/agent/debuglogs") and request.method == "POST":
                return httpx.Response(200, json={"id": 145})

            if path.endswith("/agent/debuglogs/145"):
                return httpx.Response(
                    200,
                    content=b"ZIP_DATA",
                    headers={"Content-Type": "application/zip"},
                )

            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)

        with patch("time.sleep"):
            result = client.get_execution_logs("22853")

        self.assertTrue(result.get("is_zip"))
        self.assertEqual(result.get("log_zip_content"), b"ZIP_DATA")
        self.assertTrue(any(path.endswith("/agent/debuglogs/145") for _, path in calls))
        client.close()

    def test_diagnose_new_execution_detects_other_running_process(self):
        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))

        with patch.object(
            client,
            "get_workflow_agents",
            return_value=[
                {
                    "workflow": {"name": "timesheet_report_generation_v5"},
                    "agents": [{"agentName": "agent-timesheet-01", "agentState": "RUNNING"}],
                }
            ],
        ), patch.object(
            client,
            "get_running_instances",
            return_value=[
                {
                    "id": "2590200",
                    "status": "InProgress",
                    "workflowName": "Payroll_Process",
                    "agentName": "agent-timesheet-01",
                }
            ],
        ):
            diagnosis = client.diagnose_new_execution(
                {
                    "id": "2590291",
                    "status": "New",
                    "workflowName": "timesheet_report_generation_v5",
                }
            )

        self.assertEqual(diagnosis["reason"], "other_process_running")
        self.assertEqual(diagnosis["other_execution_id"], "2590200")
        self.assertEqual(diagnosis["other_workflow_name"], "Payroll_Process")
        self.assertIn("Please wait some time", diagnosis["summary"])
        client.close()

    def test_diagnose_new_execution_detects_agent_unavailable(self):
        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))

        with patch.object(
            client,
            "get_workflow_agents",
            return_value=[
                {
                    "workflow": {"name": "timesheet_report_generation_v5"},
                    "agents": [{"agentName": "agent-timesheet-01", "agentState": "STOPPED"}],
                }
            ],
        ), patch.object(client, "get_running_instances", return_value=[]), patch.object(
            client,
            "check_agent_status",
            return_value=[],
        ):
            diagnosis = client.diagnose_new_execution(
                {
                    "id": "2590291",
                    "status": "New",
                    "workflowName": "timesheet_report_generation_v5",
                }
            )

        self.assertEqual(diagnosis["reason"], "agent_unavailable")
        self.assertIn("restart the agent", diagnosis["summary"].lower())
        client.close()

    def test_diagnose_new_execution_prioritizes_agent_unavailable_before_other_process(self):
        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))

        with patch.object(
            client,
            "get_workflow_agents",
            return_value=[
                {
                    "workflow": {"name": "timesheet_report_generation_v5"},
                    "agents": [{"agentName": "agent-timesheet-01", "agentState": "STOPPED"}],
                }
            ],
        ), patch.object(
            client,
            "get_running_instances",
            return_value=[
                {
                    "id": "2590200",
                    "status": "InProgress",
                    "workflowName": "Payroll_Process",
                    "agentName": "agent-timesheet-01",
                }
            ],
        ):
            diagnosis = client.diagnose_new_execution(
                {
                    "id": "2590291",
                    "status": "New",
                    "workflowName": "timesheet_report_generation_v5",
                }
            )

        self.assertEqual(diagnosis["reason"], "agent_unavailable")
        self.assertIn("not running", diagnosis["summary"].lower())
        client.close()

    def test_poll_execution_status_refreshes_workflow_response_from_recent_instances(self):
        calls = {"status_get": 0, "recent_list": 0}
        workflow_response = json.dumps(
            {
                "message": "The timesheet file has been shared with you. Kindly check your mailbox.",
                "error": None,
                "currentStatus": None,
                "outputParameters": [],
            }
        )

        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "abc-123"})

            if request.url.path.endswith("/workflowinstances/2609419"):
                calls["status_get"] += 1
                return httpx.Response(
                    200,
                    json={
                        "id": 2609419,
                        "status": "Complete",
                        "workflowName": "timesheet_report_generation_v5",
                    },
                )

            if request.method == "POST" and request.url.path.endswith("/workflowinstances"):
                calls["recent_list"] += 1
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 2609419,
                                "status": "Complete",
                                "workflowName": "timesheet_report_generation_v5",
                                "workflowResponse": workflow_response,
                            }
                        ]
                    },
                )

            return httpx.Response(404, json={})

        client = self._client_with_transport(handler)

        with patch("time.sleep"):
            result = client.poll_execution_status("2609419", poll_interval_sec=0, max_attempts=1)

        self.assertEqual(result["status"], "Complete")
        self.assertEqual(
            result["raw"].get("workflowResponseMessage"),
            "The timesheet file has been shared with you. Kindly check your mailbox.",
        )
        self.assertEqual(
            result["raw"].get("workflowResponseParsed", {}).get("message"),
            "The timesheet file has been shared with you. Kindly check your mailbox.",
        )
        self.assertGreaterEqual(calls["status_get"], 1)
        self.assertGreaterEqual(calls["recent_list"], 1)
        client.close()

    def test_get_required_parameters_fallback(self):
        """Verify that get_required_parameters falls back to all parameters if none are marked required."""
        client = AutomationEdgeClient()
        
        # Mock get_cached_workflow_parameters to return a schema with no 'required' flags
        schema = [
            {"name": "param1", "type": "String"},
            {"name": "param2", "type": "Number"},
        ]
        
        with patch.object(client, "get_cached_workflow_parameters", return_value=schema):
            # Test 1: No required flags -> should return all
            required = client.get_required_parameters("WF_Test")
            self.assertEqual(required, ["param1", "param2"])
            
            # Test 2: One required flag, one non-flagged -> both should be required
            schema_mixed = [
                {"name": "param1", "type": "String", "required": True},
                {"name": "param2", "type": "Number"},
            ]
            with patch.object(client, "get_cached_workflow_parameters", return_value=schema_mixed):
                required_mixed = client.get_required_parameters("WF_Mixed")
                self.assertEqual(required_mixed, ["param1", "param2"])

            # Test 3: Explicit optional flag -> should be excluded
            schema_optional = [
                {"name": "param1", "type": "String", "optional": True},
                {"name": "param2", "type": "Number", "required": False},
                {"name": "param3", "type": "String"} # Required by default
            ]
            with patch.object(client, "get_cached_workflow_parameters", return_value=schema_optional):
                required_opt = client.get_required_parameters("WF_Opt")
                self.assertEqual(required_opt, ["param3"])

            # Test 4: Catalogue style 'optional': false -> should be required
            schema_catalogue = [
                {"name": "param1", "type": "String", "optional": False},
                {"name": "param2", "type": "Number", "optional": True},
            ]
            with patch.object(client, "get_cached_workflow_parameters", return_value=schema_catalogue):
                required_cat = client.get_required_parameters("WF_Cat")
                self.assertEqual(required_cat, ["param1"])

    def test_workflow_lookup_specificity_distinguishes_followup_sentence_from_explicit_name(self):
        client = AutomationEdgeClient()

        self.assertFalse(
            client.is_specific_workflow_lookup_query(
                "retrigger this bot i have added input file"
            )
        )
        self.assertTrue(
            client.is_specific_workflow_lookup_query(
                "daily claims processing bot"
            )
        )
        self.assertTrue(
            client.is_specific_workflow_lookup_query(
                "timesheet_report_generation_v5"
            )
        )

    @patch("tools.automationedge_client.is_read_enforced", return_value=True)
    @patch("rag.engine.get_rag_engine")
    def test_resolve_workflow_via_rag_fails_closed_without_user_when_read_enforced(self, mock_get_rag, _mock_read_enforced):
        client = self._client_with_transport(lambda request: httpx.Response(404, json={}))
        mock_rag = unittest.mock.MagicMock()
        mock_get_rag.return_value = mock_rag

        result = client.resolve_workflow_via_rag("claims status")

        self.assertEqual(result, "")
        mock_rag.search_tools.assert_not_called()
        client.close()


if __name__ == "__main__":
    unittest.main()
