
import logging
import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.agent_router import AgentRouter
from agents.agent_registry import get_agent_registry
from agents.diagnostic_agent import DiagnosticAgent
from agents.remediation_agent import RemediationAgent
from agents.orchestrator_agent import OrchestratorAgent
from state.conversation_state import ConversationState, ConversationPhase

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

def test_routing_fast():
    registry = get_agent_registry()
    
    # Mock handle methods to avoid real tool calls
    diag = DiagnosticAgent()
    diag.handle = MagicMock(return_value=MagicMock(response="Mock Diag Response"))
    
    remedy = RemediationAgent()
    remedy.handle = MagicMock(return_value=MagicMock(response="Mock Remedy Response"))
    
    orch = OrchestratorAgent()
    orch.handle = MagicMock(return_value=MagicMock(response="Mock Orch Response"))

    # Re-register with mocks
    registry.register(diag)
    registry.register(remedy)
    registry.register(orch)

    router = AgentRouter(registry=registry)

    test_cases = [
        {
            "msg": "Which workflows are having issues?",
            "expected": "diagnostic_agent",
            "desc": "Keyword 'issues' and 'workflow' (domain) should favor diagnostic"
        },
        {
            "msg": "Trigger workflow add_employee",
            "expected": "remediation_agent",
            "desc": "Keyword 'trigger' and 'workflow' (domain) should favor remediation"
        },
        {
            "msg": "2026-03-03 to 2026-03-16",
            "history": [{"role": "assistant", "content": "Would you like to retrieve the logs for this agent?"}],
            "expected": "diagnostic_agent",
            "desc": "Context-aware routing for date ranges (history match)"
        },
        {
            "msg": "Yes, proceed with the fix",
            "history": [{"role": "assistant", "content": "I found an issue. Should I restart the workflow?"}],
            "phase": ConversationPhase.AWAITING_APPROVAL,
            "expected": "remediation_agent",
            "desc": "Approval turn should stay with remediation (history match + phase)"
        },
        {
            "msg": "Hi, who are you?",
            "expected": "ops_orchestrator",
            "desc": "Generic greeting should fall back to orchestrator"
        }
    ]

    print("\n--- Starting FAST Routing Verification ---\n")
    passed = 0
    errors = []
    for i, tc in enumerate(test_cases):
        print(f"Test {i+1}: {tc['desc']}")
        print(f"  Input: '{tc['msg']}'")
        
        state = ConversationState()
        state.conversation_id = "test_conv"
        if "history" in tc:
            for m in tc["history"]:
                state.add_message(m["role"], m["content"])
        if "phase" in tc:
            state.phase = tc["phase"]

        result = router.route(
            user_message=tc["msg"],
            conversation_id="test_conv",
            state=state
        )
        
        # We need to know which agent was actually CALLED.
        # MagicMock.called tells us if it was called.
        if tc["expected"] == "diagnostic_agent" and diag.handle.called:
            print("  SUCCESS: Routed to diagnostic_agent")
            passed += 1
        elif tc["expected"] == "remediation_agent" and remedy.handle.called:
            print("  SUCCESS: Routed to remediation_agent")
            passed += 1
        elif tc["expected"] == "ops_orchestrator" and orch.handle.called:
            print("  SUCCESS: Routed to ops_orchestrator")
            passed += 1
        else:
            print(f"  FAILURE: Expected {tc['expected']} but it was not called.")
            errors.append(f"Test {i+1} failed")
        
        # Reset mocks for next test
        diag.handle.reset_mock()
        remedy.handle.reset_mock()
        orch.handle.reset_mock()

    print(f"\nCompleted {len(test_cases)} tests. Passed: {passed}, Failed: {len(errors)}")
    if errors:
        sys.exit(1)

if __name__ == "__main__":
    test_routing_fast()
