"""
Tests for dynamic AutomationEdge tool mapping and registry reload.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CONFIG
from tools.base import ToolDefinition
from tools.registry import ToolRegistry


class _FakeAEClient:
    def list_workflows(self):
        return [
            {
                "id": "wf-1",
                "name": "FileWriterWorkflow",
            }
        ]

    def get_workflow_details(self, workflow_identifier: str):
        return {
            "id": workflow_identifier,
            "workflowName": "FileWriterWorkflow",
            "agenticAiToolConfiguration": {
                "toolName": "write_file_tool",
                "toolDescription": "Write a file via AE workflow",
                "status": "active",
                "category": "file_ops",
                "tags": ["files", "automationedge"],
            },
            "configurationParameters": [
                {
                    "name": "targetPath",
                    "type": "String",
                    "required": True,
                    "description": "Path to write to",
                },
                {
                    "name": "overwrite",
                    "type": "Boolean",
                    "required": False,
                    "description": "Overwrite existing file",
                },
            ],
        }

    def execute_workflow(self, **kwargs):
        return {
            "status": "QUEUED",
            "requestId": "REQ-200",
            "workflowName": kwargs.get("workflow_name"),
        }


class TestDynamicAETools(unittest.TestCase):
    def setUp(self):
        self._backup = dict(CONFIG)
        CONFIG["AE_ENABLE_DYNAMIC_TOOLS"] = True

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._backup)

    def test_reload_registers_dynamic_ae_tool(self):
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="check_workflow_status",
                description="Static",
                category="status",
                tier="read_only",
            ),
            lambda **_: {"success": True},
        )

        with patch("tools.registry.get_automationedge_client", return_value=_FakeAEClient()):
            summary = registry.reload_automationedge_tools()

        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["registered"], 1)
        self.assertIn("write_file_tool", registry.list_tools())
        tool = registry.get_tool("write_file_tool")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.category, "file_ops")
        self.assertIn("targetPath", tool.parameters)

        result = registry.execute("write_file_tool", targetPath="/tmp/out.txt")
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("requestId"), "REQ-200")


if __name__ == "__main__":
    unittest.main()

