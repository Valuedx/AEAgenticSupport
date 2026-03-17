
import sys
import os
import json
from unittest.mock import MagicMock, patch

# Force UTF-8 for stdout
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Mock RAG hit data
mock_hit = {
    "name": "Maturity_Claim",
    "workflow_name": "Maturity_Claim",
    "category": "automationedge",
    "score": 0.5,
    "metadata": {
        "source": "automationedge",
        "parameters": [
            {
                "name": "Input_Path",
                "type": "File",
                "extension": ".csv",
                "displayName": "Input Path"
            }
        ]
    }
}

# Mock Tool Result
class MockToolResult:
    def __init__(self, data, success=True):
        self.data = data
        self.success = success

# Mock AE client
class MockAEClient:
    def get_cached_workflow_parameters(self, name):
        if name == "Maturity_Claim":
            return [
                {
                    "name": "Input_Path",
                    "type": "File",
                    "extension": ".csv",
                    "displayName": "Input Path"
                }
            ]
        return []

# Mock llm_client to capture prompts
captured_prompts = []
class MockLLM:
    def chat(self, prompt, **kwargs):
        captured_prompts.append(prompt)
        if "Classify" in prompt:
            return "EXECUTE"
        return "NATURAL_RESPONSE"

import config.llm_client
config.llm_client.llm_client = MockLLM()

# Mock tool_registry
import tools.registry
mock_registry = MagicMock()
mock_registry.execute.return_value = MockToolResult({"tools": [mock_hit]})
tools.registry.tool_registry = mock_registry

with patch("agents.orchestrator.get_ae_client", return_value=MockAEClient()):
    from agents.orchestrator import Orchestrator
    from state.conversation_state import ConversationState

    def verify_mc_flow():
        orch = Orchestrator()
        state = ConversationState()
        state.conversation_id = "test_mc"
        
        print("Testing Preflight for 'Maturity_Claim'...")
        resp = orch._preflight_workflow_param_collection(
            user_message="i want to run maturity claim bot",
            state=state
        )
        
        print(f"\nTotal LLM calls: {len(captured_prompts)}")
        
        found_metadata = False
        for i, prompt in enumerate(captured_prompts):
            print(f"\n--- PROMPT #{i+1} ---")
            # Print only first 200 chars to avoid noise
            print(prompt[:500] + "...") 
            if "Input Path (File Upload, format: .csv)" in prompt:
                found_metadata = True

        if found_metadata:
            print("\nSUCCESS: File metadata correctly included in the LLM prompt!")
        else:
            print("\nFAILURE: Metadata hint missing from all LLM prompts.")

    if __name__ == "__main__":
        verify_mc_flow()
