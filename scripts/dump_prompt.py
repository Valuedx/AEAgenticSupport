
# -*- coding: utf-8 -*-
import os
import sys
import json
import io

# Force UTF-8 for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState
from state.issue_tracker import IssueTracker

def dump_prompt():
    orch = Orchestrator()
    state = ConversationState()
    state.conversation_id = "test_conv"
    tracker = IssueTracker("test_conv")
    
    # Mock some state
    state.add_message("user", "I want to add an employee")
    # Simulate a partial param collection to see the hint
    state.param_collection = {
        "workflow_name": "WF_add_employee",
        "collected_params": {"emp_id": "E123"}
    }
    
    prompt = orch._build_system_prompt(state, tracker)
    with open("prompt_dump.txt", "w", encoding="utf-8") as f:
        f.write(prompt)
    print("=== SYSTEM PROMPT DUMPED TO prompt_dump.txt ===")

if __name__ == "__main__":
    dump_prompt()
