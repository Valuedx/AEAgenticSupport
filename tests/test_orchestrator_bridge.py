"""Tests for Studio ↔ visual orchestrator bridge (client + payload merge)."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CONFIG
from gateway.message_gateway import MessageGateway
from tools.orchestrator_client import OrchestratorClient


class TestMergeOrchestratorTriggerPayload(unittest.TestCase):
    def test_fills_message_and_session_when_missing(self):
        out = MessageGateway._merge_orchestrator_trigger_payload(
            {"foo": 1},
            user_message="  hello  ",
            conversation_id="sess-9",
            user_id="u1",
            user_role="technical",
            user_name="Pat",
            user_email="p@x.com",
        )
        self.assertEqual(out["foo"], 1)
        self.assertEqual(out["message"], "hello")
        self.assertEqual(out["session_id"], "sess-9")
        self.assertEqual(out["user_id"], "u1")
        self.assertEqual(out["user_role"], "technical")
        self.assertEqual(out["user_name"], "Pat")
        self.assertEqual(out["user_email"], "p@x.com")

    def test_does_not_override_explicit_keys(self):
        out = MessageGateway._merge_orchestrator_trigger_payload(
            {
                "message": "from_payload",
                "session_id": "fixed",
                "user_id": "x",
            },
            user_message="chat",
            conversation_id="other",
            user_id="u1",
            user_role="technical",
        )
        self.assertEqual(out["message"], "from_payload")
        self.assertEqual(out["session_id"], "fixed")
        self.assertEqual(out["user_id"], "x")


class TestOrchestratorClient(unittest.TestCase):
    def setUp(self):
        self._backup = dict(CONFIG)
        CONFIG["ORCHESTRATOR_BASE_URL"] = "http://orch.test"
        CONFIG["ORCHESTRATOR_TENANT_ID"] = "tenant-a"
        CONFIG["ORCHESTRATOR_API_TOKEN"] = ""

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._backup)

    def test_headers_include_bearer_when_token_set(self):
        CONFIG["ORCHESTRATOR_API_TOKEN"] = "secret-token"
        c = OrchestratorClient()
        h = c._headers()
        self.assertEqual(h["Authorization"], "Bearer secret-token")
        self.assertEqual(h["X-Tenant-Id"], "tenant-a")
        c.close()

    def test_execute_maps_401_to_operator_hint(self):
        def handler(request: httpx.Request):
            if request.method == "POST" and request.url.path.endswith("/execute"):
                return httpx.Response(
                    401,
                    json={"detail": "Invalid or expired token"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport, base_url="http://orch.test")
        c = OrchestratorClient(http_client=http)
        with self.assertRaises(RuntimeError) as ar:
            c.execute("wf-1", {"a": 1})
        msg = str(ar.exception)
        self.assertIn("401", msg)
        self.assertIn("ORCHESTRATOR_API_TOKEN", msg)
        self.assertIn("Invalid or expired token", msg)
        c.close()

    def test_run_and_wait_completed(self):
        calls = {"ctx": 0}

        def handler(request: httpx.Request):
            if request.method == "POST" and request.url.path.endswith("/execute"):
                return httpx.Response(201, json={"id": "inst-1"})
            if request.method == "GET" and "/context" in request.url.path:
                calls["ctx"] += 1
                if calls["ctx"] < 2:
                    body = {
                        "instance_id": "inst-1",
                        "status": "running",
                        "current_node_id": "node_1",
                        "approval_message": None,
                        "context_json": {},
                    }
                else:
                    body = {
                        "instance_id": "inst-1",
                        "status": "completed",
                        "current_node_id": None,
                        "approval_message": None,
                        "context_json": {"node_2": {"response": "done"}},
                    }
                return httpx.Response(200, json=body)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport, base_url="http://orch.test")
        c = OrchestratorClient(http_client=http)
        with patch("tools.orchestrator_client._POLL_INTERVAL", 0):
            ctx = c.run_and_wait("wf-1", {"message": "hi"}, timeout=5)
        self.assertEqual(ctx["status"], "completed")
        self.assertEqual(ctx["context_json"]["node_2"]["response"], "done")
        c.close()

    def test_run_and_wait_return_on_suspended(self):
        def handler(request: httpx.Request):
            if request.method == "POST" and request.url.path.endswith("/execute"):
                return httpx.Response(201, json={"id": "inst-s"})
            if "/context" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "instance_id": "inst-s",
                        "status": "suspended",
                        "current_node_id": "node_h",
                        "approval_message": "OK to proceed?",
                        "context_json": {"trigger": {"message": "x"}},
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport, base_url="http://orch.test")
        c = OrchestratorClient(http_client=http)
        with patch("tools.orchestrator_client._POLL_INTERVAL", 0):
            ctx = c.run_and_wait("wf-1", {}, timeout=5, return_on_suspended=True)
        self.assertEqual(ctx["status"], "suspended")
        self.assertEqual(ctx["approval_message"], "OK to proceed?")
        c.close()

    def test_run_and_wait_raises_on_suspended_by_default(self):
        def handler(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(201, json={"id": "inst-s"})
            return httpx.Response(
                200,
                json={
                    "instance_id": "inst-s",
                    "status": "suspended",
                    "current_node_id": None,
                    "approval_message": None,
                    "context_json": {},
                },
            )

        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport, base_url="http://orch.test")
        c = OrchestratorClient(http_client=http)
        with patch("tools.orchestrator_client._POLL_INTERVAL", 0):
            with self.assertRaises(RuntimeError) as ar:
                c.run_and_wait("wf-1", {}, timeout=5)
            self.assertIn("suspended", str(ar.exception).lower())
        c.close()


class TestOrchestratorBridgeWaitForResult(unittest.TestCase):
    def setUp(self):
        self._backup = dict(CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._backup)

    def test_uses_config_when_metadata_key_absent(self):
        CONFIG["ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT"] = True
        self.assertTrue(
            MessageGateway._orchestrator_bridge_wait_for_result({"other": 1})
        )
        CONFIG["ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT"] = False
        self.assertFalse(
            MessageGateway._orchestrator_bridge_wait_for_result({"other": 1})
        )

    def test_metadata_overrides_config(self):
        CONFIG["ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT"] = False
        self.assertTrue(
            MessageGateway._orchestrator_bridge_wait_for_result(
                {"orchestrator_wait_for_result": "true"}
            )
        )
        CONFIG["ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT"] = True
        self.assertFalse(
            MessageGateway._orchestrator_bridge_wait_for_result(
                {"orchestrator_wait_for_result": "0"}
            )
        )


class TestMessageGatewayBridgeAsyncSync(unittest.TestCase):
    def setUp(self):
        self._backup = dict(CONFIG)
        CONFIG["ORCHESTRATOR_BASE_URL"] = "http://orch.test"
        CONFIG["ORCHESTRATOR_TENANT_ID"] = "tenant-a"
        CONFIG["ORCHESTRATOR_API_TOKEN"] = ""
        CONFIG["ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT"] = False

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._backup)
        import tools.orchestrator_client as oc

        oc._singleton = None

    @patch("tools.orchestrator_client.get_orchestrator_client")
    def test_async_default_execute_only(self, mock_get):
        mock_client = MagicMock()
        mock_client.base_url = "http://orch.test"
        mock_client.execute.return_value = {"id": "inst-async-1"}
        mock_get.return_value = mock_client

        gw = MessageGateway()
        out = gw.process_message(
            "sess-x",
            "trigger text",
            user_metadata={"orchestrator_workflow_id": "11111111-1111-1111-1111-111111111111"},
        )
        mock_client.execute.assert_called_once()
        args, kwargs = mock_client.execute.call_args
        self.assertEqual(args[0], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            args[1]["message"],
            "trigger text",
        )
        self.assertEqual(args[1]["session_id"], "sess-x")
        mock_client.run_and_wait.assert_not_called()
        self.assertIn("inst-async-1", out)
        self.assertIn("non-blocking", out.lower())

    @patch("tools.orchestrator_client.get_orchestrator_client")
    def test_sync_when_wait_for_result_in_metadata(self, mock_get):
        mock_client = MagicMock()
        mock_client.run_and_wait.return_value = {
            "status": "completed",
            "context_json": {"node_a": {"ok": True}},
        }
        mock_get.return_value = mock_client

        gw = MessageGateway()
        out = gw.process_message(
            "c2",
            "m2",
            user_metadata={
                "orchestrator_workflow_id": "22222222-2222-2222-2222-222222222222",
                "orchestrator_wait_for_result": True,
            },
        )
        mock_client.execute.assert_not_called()
        mock_client.run_and_wait.assert_called_once()
        r_args, r_kw = mock_client.run_and_wait.call_args
        self.assertEqual(r_args[0], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(r_args[1]["message"], "m2")
        self.assertEqual(r_args[1]["session_id"], "c2")
        self.assertEqual(r_kw["timeout"], 120)
        self.assertTrue(r_kw["return_on_suspended"])
        self.assertIn("completed", out.lower())
        self.assertIn("node_a", out)

    @patch("tools.orchestrator_client.get_orchestrator_client")
    def test_sync_when_config_wait_for_result_true(self, mock_get):
        CONFIG["ORCHESTRATOR_BRIDGE_WAIT_FOR_RESULT"] = True
        mock_client = MagicMock()
        mock_client.run_and_wait.return_value = {
            "status": "completed",
            "context_json": {},
        }
        mock_get.return_value = mock_client

        gw = MessageGateway()
        gw.process_message(
            "c3",
            "",
            user_metadata={
                "orchestrator_workflow_id": "33333333-3333-3333-3333-333333333333",
            },
        )
        mock_client.run_and_wait.assert_called_once()
        mock_client.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
