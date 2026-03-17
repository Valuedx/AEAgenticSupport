
import os
import sys
import logging

# Ensure local imports work
sys.path.insert(0, os.getcwd())

from tools.bootstrap import initialize_tooling
from gateway.message_gateway import MessageGateway

def test_mcp_query():
    print("Initializing tooling...")
    initialize_tooling()
    
    gateway = MessageGateway()
    
    # This query targets the MCP tool ae.request.get_summary
    user_query = "Give me a detailed summary for request 2501865"
    print(f"\nProcessing Query: '{user_query}'")
    
    # We use a fresh conversation ID to avoid state interference
    response = gateway.process_message(
        conversation_id="mcp-test-session-999",
        user_message=user_query,
        user_id="tester",
        user_role="technical"
    )
    
    print("\n" + "="*50)
    print("AGENT RESPONSE:")
    print("="*50)
    print(response)
    print("="*50)

if __name__ == "__main__":
    test_mcp_query()
