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

    def test_get_workflow_latest_instance_uses_modern_status_endpoint(self):
        def handler(request: httpx.Request):
            if request.url.path.endswith("/authenticate"):
                return httpx.Response(200, json={"token": "tok-1"})
            if request.url.path.endswith("/api/v1/workflows/Policy_Renewal_Batch/status"):
                return httpx.Response(
                    200,
                    json={
                        "workflow_name": "Policy_Renewal_Batch",
                        "status": "active",
                        "errorMessage": "Input file missing",
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


if __name__ == "__main__":
    unittest.main()
