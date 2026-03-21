
import sys
import os
import io
import json
from unittest.mock import MagicMock, patch

# Force UTF-8 for stdout
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Mock llm_client
class MockLLM:
    def chat(self, prompt, **kwargs):
        return "MOCK_RESPONSE"

import config.llm_client
config.llm_client.llm_client = MockLLM()

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from state.issue_tracker import IssueTracker

def test_robust_context_extraction():
    orch = Orchestrator()
    state = ConversationState()
    state.conversation_id = "test-context-conv"
    
    # Add dummy messages to trigger ConversationState.get_recent_context_summary
    state.add_message("user", "how is my agent doing?")
    state.add_message("assistant", "Found a running agent.")

    # Simulate a tool call result that is a LIST (like ae.agent.list_running)
    # This was failing before because it only handled dicts
    state.tool_call_log.append({
        "tool": "ae.agent.list_running",
        "params": {},
        "result": [
            {"id": "2887", "name": "omkar.patil@VDXLPT-1569", "status": "RUNNING"}
        ],
        "success": True,
        "timestamp": "2026-03-16T22:00:00"
    })
    
    tracker = IssueTracker(state.conversation_id)
    
    print("Testing Robust Tool Context Extraction...")
    system_prompt = orch._build_system_prompt(state, tracker)
    
    # Check block 1: Recent Conversation Context (from ConversationState)
    if "## Recent Conversation Context" in system_prompt:
        # Check if 'Agent Id' is correctly labeled (not 'Schedule Id')
        if "Agent Id: **2887**" in system_prompt:
            print("SUCCESS: agent_id correctly extracted in Recent Conversation Context.")
        elif "Schedule Id: **2887**" in system_prompt:
            print("FAILURE: agent_id mislabeled as Schedule Id in Recent Conversation Context.")
        else:
            print("FAILURE: agent_id MISSING from Recent Conversation Context.")
    else:
        print("FAILURE: 'Recent Conversation Context' block not found.")

    # Check block 2: Recent Tool Findings (immediate findings)
    if "## Recent Tool Findings" in system_prompt:
        if "- agent_id: 2887" in system_prompt:
            print("SUCCESS: agent_id correctly extracted in Recent Tool Findings.")
        else:
            print("FAILURE: agent_id MISSING from Recent Tool Findings.")
    else:
        print("FAILURE: 'Recent Tool Findings' block not found.")

    # Check for the proactive logs mandate
    proactive_mandate = 'CRITICAL: If an `agent_id` is listed above and the user asks for logs'
    if proactive_mandate in system_prompt:
        print("SUCCESS: Proactive logs mandate found in system prompt.")
    else:
        print("FAILURE: Proactive logs mandate NOT found.")

if __name__ == "__main__":
    test_robust_context_extraction()
