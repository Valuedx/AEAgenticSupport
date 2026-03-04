"""
Unit tests for the AutomationEdge REST client.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

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

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
