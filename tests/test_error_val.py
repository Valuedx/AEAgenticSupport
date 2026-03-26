
import sys
import os
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.orchestrator import Orchestrator

class TestErrorPropagation(unittest.TestCase):
    def setUp(self):
        self.orchestrator = Orchestrator()
    
    def test_build_action_failure_response_includes_error(self):
        tool = "trigger_workflow"
        args = {"workflow_name": "LogExtractionAndRecognition"}
        error = "🚫 **LogExtractionAndRecognition** cannot be triggered because all assigned agents are currently offline or stopped (adars@NEXUS)."
        
        # Mock _get_sop_troubleshooting_steps to avoid RAG calls
        self.orchestrator._get_sop_troubleshooting_steps = MagicMock(return_value=["Check agent status", "Restart agent"])
        
        response = self.orchestrator._build_action_failure_response(
            action_tool=tool,
            action_args=args,
            error_text=error
        )
        
        print("\nGenerated Response:\n", response)
        
        self.assertIn("Error details", response)
        self.assertIn(error, response)
        self.assertIn("LogExtractionAndRecognition", response)
        self.assertIn("Check agent status", response)

if __name__ == "__main__":
    unittest.main()
