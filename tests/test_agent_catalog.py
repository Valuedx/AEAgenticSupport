"""
Tests for agent definition and interaction catalog.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.agent_catalog import AgentCatalog


class TestAgentCatalog(unittest.TestCase):
    def test_upsert_and_interaction_linking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent_catalog.json")
            catalog = AgentCatalog(path=path, max_events=50)

            created = catalog.upsert_agent(
                {
                    "agentId": "claims_agent",
                    "name": "Claims Agent",
                    "usecase": "claims_recovery",
                    "linkedTools": ["write_file_tool", "check_workflow_status"],
                    "tags": ["claims"],
                }
            )
            self.assertEqual(created["agentId"], "claims_agent")
            self.assertIn("write_file_tool", created["linkedTools"])

            catalog.log_tool_interaction(
                tool_name="write_file_tool",
                params={"targetPath": "/tmp/a.txt"},
                success=True,
            )
            rows = catalog.list_interactions(agent_id="claims_agent", limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["toolName"], "write_file_tool")
            self.assertTrue(rows[0]["success"])

            catalog.log_tool_interaction(
                tool_name="unknown_tool",
                params={},
                success=False,
                error="no mapping",
            )
            unmapped = catalog.list_interactions(agent_id="unmapped", limit=5)
            self.assertEqual(len(unmapped), 1)
            self.assertFalse(unmapped[0]["success"])


if __name__ == "__main__":
    unittest.main()

