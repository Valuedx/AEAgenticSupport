
import os
import sys
import logging
import uuid
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState, ConversationPhase
from config.settings import CONFIG

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reproduce")

def run_test(prompt, cid=None):
    if not cid:
        cid = f"test-{uuid.uuid4().hex[:8]}"
    
    state = ConversationState.load(cid)
    orchestrator = Orchestrator()
    
    print(f"\n--- Testing PROMPT: '{prompt}' (CID: {cid}) ---")
    
    # Check RAG similarity manually first
    from rag.engine import get_rag_engine
    rag = get_rag_engine()
    query_vec = rag.embed_query(prompt)
    hits = rag.search_tools(prompt, top_k=5, query_embedding=query_vec)
    
    best_sim = 0.0
    for h in hits:
        sim = h.get("similarity", 0.0)
        print(f"  Tool Hit: {h.get('id')} - Similarity: {sim:.4f}")
        best_sim = max(best_sim, sim)
    
    print(f"  Best Similarity: {best_sim:.4f} (Threshold is 0.45)")
    
    # Check routing
    route = orchestrator._classify_conversational_route(prompt)
    print(f"  Classified Route: {route}")
    
    # Handle message
    response = orchestrator.handle_message(prompt, state)
    print(f"  FINAL RESPONSE: {response}")
    print(f"  State Phase: {state.phase.value}")
    print(f"  History Length: {len(state.messages)}")

if __name__ == "__main__":
    # Test cases based on user reports
    # Case 1: Initial prompt
    run_test("i want to add one employee")
    
    # Case 2: Status check
    run_test("can you check agent status")
    
    # Case 3: Follow-up (simulated)
    cid = f"test-followup-{uuid.uuid4().hex[:4]}"
    run_test("i want to add one employee", cid=cid)
    run_test("what understood??", cid=cid)
