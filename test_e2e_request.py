
import os
import sys
import logging
import json

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gateway.message_gateway import MessageGateway
from state.conversation_state import ConversationState
from tools.bootstrap import initialize_tooling

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("e2e_test")

def run_e2e_test():
    # Initialize tools
    print("Initializing tooling...")
    initialize_tooling()
    
    gateway = MessageGateway()
    
    user_message = "can you check status of request id 2501865"
    import uuid
    conversation_id = f"e2e-test-request-{uuid.uuid4().hex[:8]}"
    print(f"Conversation ID: {conversation_id}")

    print(f"\nProcessing Query: '{user_message}'")
    
    # We use process_message which triggers the full loop
    try:
        response = gateway.process_message(
            conversation_id=conversation_id,
            user_message=user_message,
            user_id="e2e_tester",
            user_role="technical"
        )
        
        print("\n" + "="*50)
        print("FINAL AGENT RESPONSE:")
        print("="*50)
        print(response)
        print("="*50)
        
    except Exception as e:
        print(f"\nError during e2e test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_e2e_test()
