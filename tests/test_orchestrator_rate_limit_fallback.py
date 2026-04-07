from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState


class TestOrchestratorRateLimitFallback(unittest.TestCase):
    def setUp(self):
        self.orchestrator = Orchestrator()

    def test_is_rate_limit_error_detects_nested_resource_exhausted(self):
        try:
            try:
                raise RuntimeError("429 RESOURCE_EXHAUSTED from Vertex AI")
            except RuntimeError as inner:
                raise RuntimeError("RetryError wrapper") from inner
        except RuntimeError as exc:
            self.assertTrue(self.orchestrator._is_rate_limit_error(exc))

    def test_build_recent_tool_result_fallback_prefers_tool_report(self):
        state = ConversationState()
        state.tool_call_log.append(
            {
                "tool": "get_execution_logs",
                "success": True,
                "result": {
                    "success": True,
                    "execution_id": "22821",
                    "workflow_name": "TEBT_Workflow",
                    "report": "### Execution Log Analysis\nExecution ID: `22821`\nRoot cause identified.",
                },
            }
        )

        fallback = self.orchestrator._build_recent_tool_result_fallback_response(state)

        self.assertIn("Execution Log Analysis", fallback)
        self.assertIn("22821", fallback)


if __name__ == "__main__":
    unittest.main()
