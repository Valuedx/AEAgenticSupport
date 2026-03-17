
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from unittest.mock import MagicMock

def verify_rules():
    print("--- Verifying Orchestrator Rules ---")
    orch = Orchestrator()
    state = ConversationState()
    state.conversation_id = "test-conv"
    tracker = MagicMock()
    
    prompt = orch._build_system_prompt(state, tracker)
    
    # Check for Rule 16
    print("Checking Rule 16 (Proactive Discovery)...")
    if "PROACTIVE DIAGNOSTIC DISCOVERY" in prompt and "agent_id=\"\"" in prompt:
        print("SUCCESS: Rule 16 Found")
    else:
        print("FAILURE: Rule 16 Missing or Incorrect")

    # Check for Rule 17
    print("Checking Rule 17 (Log Date Selection)...")
    if "LOG DATE SELECTION RULE" in prompt and "last 24 hours" in prompt:
        print("SUCCESS: Rule 17 Found")
    else:
        print("FAILURE: Rule 17 Missing or Incorrect")
        # Print a snippet of the prompt to see what's there
        start = prompt.find("16.")
        print("Prompt Snippet:\n", prompt[start:start+500] if start != -1 else "Rule 16 not found even by index")

    # Check for Name Resolution instruction
    if "NAME RESOLUTION" in prompt and "agent NAME" in prompt:
        print("SUCCESS: Name Resolution Instruction Found")
    else:
        print("FAILURE: Name Resolution Instruction Missing")

if __name__ == "__main__":
    verify_rules()
