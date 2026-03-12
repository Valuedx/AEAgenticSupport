
import os
import sys
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock objects needed for testing
class MockState:
    def __init__(self):
        self.preferred_language = "en"
        self.user_role = "technical"
        self.messages = []
        self.tool_call_log = []
    
    def get_recent_context_summary(self, n_turns=5):
        return "Recent context summary..."

class MockTracker:
    def __init__(self):
        self.issues = {}
    def get_active_issue(self):
        return None
    def get_all_issues_summary(self):
        return "Issue summary..."

# Test _build_system_prompt
def test_system_prompt():
    from agents.orchestrator import Orchestrator
    orch = Orchestrator()
    state = MockState()
    tracker = MockTracker()
    
    prompt = orch._build_system_prompt(state, tracker)
    print("--- System Prompt Extract ---")
    # Look for the time context
    if "## Current Time:" in prompt:
        print("SUCCESS: Time context found in system prompt.")
        # Extract the line
        for line in prompt.splitlines():
            if "## Current Time:" in line:
                print(f"Time in prompt: {line}")
    else:
        print("FAILED: Time context NOT found in system prompt.")

# Test _build_conversational_response
# (This one actually calls the LLM, so we'll just check if the call is constructed with the time)
import unittest.mock as mock

def test_conversational_response_construction():
    from agents.orchestrator import Orchestrator
    orch = Orchestrator()
    state = MockState()
    tracker = MockTracker()
    
    with mock.patch('config.llm_client.llm_client.chat') as mock_chat:
        orch._build_conversational_response(
            user_message="Good morning",
            route="SMALLTALK",
            tracker=tracker
        )
        # Note: _build_conversational_response for route="SMALLTALK" returns a static string
        # Let's test with route="GENERAL" which calls llm_client.chat
        mock_chat.return_value = "Mocked Response"
        orch._build_conversational_response(
            user_message="Tell me a joke",
            route="GENERAL",
            tracker=tracker
        )
        
        args, kwargs = mock_chat.call_args
        prompt = args[0]
        if "Current Date/Time:" in prompt:
            print("SUCCESS: Time context found in conversational prompt construction.")
        else:
            print("FAILED: Time context NOT found in conversational prompt construction.")

if __name__ == "__main__":
    test_system_prompt()
    test_conversational_response_construction()
