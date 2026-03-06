
import logging
from agents.agent_router import get_agent_router
from agents.agent_registry import get_agent_registry
from state.conversation_state import ConversationState
from gateway.progress import create_noop_progress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_a2a")

def test_a2a_delegation():
    router = get_agent_router()
    registry = get_agent_registry()
    
    # Ensure all agents are registered (usually handled in gateway lazy load, but let's be explicit)
    from agents.orchestrator_agent import OrchestratorAgent
    from agents.diagnostic_agent import DiagnosticAgent
    from agents.remediation_agent import RemediationAgent
    
    if not registry.get("ops_orchestrator"):
        registry.register(OrchestratorAgent())
    if not registry.get("diagnostic_agent"):
        registry.register(DiagnosticAgent())
    if not registry.get("remediation_agent"):
        registry.register(RemediationAgent())

    state = ConversationState()
    state.conversation_id = "test_a2a_handoff"
    state.user_id = "tester"
    
    # Path B: Force Orchestrator first to test A2A delegation logic
    query_b = "I need to restart the Workflow-X"
    logger.info(f"--- Processing query (A2A): {query_b} ---")
    result_b = router.dispatch_to(
        agent_id="ops_orchestrator",
        user_message=query_b,
        conversation_id=state.conversation_id,
        user_id=state.user_id,
        state=state,
        on_progress=create_noop_progress()
    )
    
    logger.info(f"Final Response (A2A):\n{result_b.response}")
    
    # Check for delegation markers
    assert "remediation_agent" in result_b.metadata.get("delegation_chain", [])
    assert "handing this over" in result_b.response.lower()

if __name__ == "__main__":
    try:
        test_a2a_delegation()
        print("A2A Delegation Test PASSED")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
