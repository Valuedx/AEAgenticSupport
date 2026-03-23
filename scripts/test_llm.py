import sys
import os
sys.path.insert(0, os.getcwd())
from config.llm_client import llm_client, set_current_trace
from state.conversation_state import ConversationState

from config.observability import trace_context

# Mock state
state = ConversationState()
state.user_role = "technical"
state.conversation_id = "test-langfuse-001"

print("Testing direct chat with Langfuse tracing...")
with trace_context(
    "test_llm_direct",
    session_id=state.conversation_id,
    user_id="test_user",
    tags=["test", "CLI"]
) as trace:
    set_current_trace(trace)
    resp = llm_client.chat(
        "Respond naturally and briefly to the user's general message.\n"
        "Do not mention tools, workflows, SOPs, or incidents.\n"
        "End with one short line that you can also help with AutomationEdge issues.\n"
        "Route: GENERAL\n"
        'User message: "What workflows are having issues?"',
        system="You are a polite, concise assistant.",
        max_tokens=120
    )
    set_current_trace(None)
    if trace:
        trace.update(output={"response": resp})

print(f"Response: {resp!r}")
print(f"Length: {len(resp)}")

print("\nTesting classification...")
route = llm_client.chat(
    (
        "Classify this user message for routing.\n"
        "Return exactly one token: ACK, SMALLTALK, GENERAL, or OPS.\n"
        "ACK = brief acknowledgement or thanks.\n"
        "SMALLTALK = greeting/chit-chat without a task.\n"
        "GENERAL = non-ops/general question not requiring workflows/tools/SOP.\n"
        "OPS = any operations/support/troubleshooting/automation workflow intent.\n"
        "Active issue status: none\n"
        "Tool relevance score (0-1): 0.000\n"
        'User message: "What workflows are having issues?"'
    ),
    system="Be strict and output one token only.",
    temperature=0.0,
    max_tokens=8,
).strip().upper()
print(f"Route: {route!r}")
