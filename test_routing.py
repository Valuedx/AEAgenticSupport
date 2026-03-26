
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath("."))

from state.conversation_state import ConversationState as ConvState
from agents.agent_router import _score_agent
from agents.orchestrator_agent import OrchestratorAgent
from agents.diagnostic_agent import DiagnosticAgent

def test_routing_stickiness():
    state = ConvState()
    # Simulate that Orchestrator was the last agent
    state.last_agent_id = "ops_orchestrator"
    
    # Message that contains keywords for Diagnostic Agent ("failed")
    msg = "The Test_DiskSpaceCleanup workflow failed during execution. The request ID for this execution is 2564846"
    
    orch = OrchestratorAgent()
    diag = DiagnosticAgent()
    
    # In my new scoring:
    # DiagnosticAgent.can_handle returns 0.7 for "failed"
    # OrchestratorAgent.can_handle returns 0.4
    # _score_agent adds stickiness_bonus 0.35 if state.last_agent_id match
    
    orch_score = _score_agent(orch, msg, {}, state=state)
    diag_score = _score_agent(diag, msg, {}, state=state)
    
    print(f"Message: {msg}")
    print(f"Orchestrator Score (Base 0.4 + Sticky 0.35): {orch_score}")
    print(f"Diagnostic Score (Keyword 0.7): {diag_score}")
    
    if orch_score > diag_score:
        print("SUCCESS: Orchestrator wins (0.75 > 0.7) due to stickiness!")
    else:
        print(f"FAILURE: Diagnostic agent wins ({diag_score} >= {orch_score}).")

if __name__ == "__main__":
    test_routing_stickiness()
