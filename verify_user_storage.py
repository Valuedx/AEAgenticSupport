import os
import sys
import json
import psycopg2
from datetime import datetime

# Add project root to path
sys.path.insert(0, r"d:\AEAgenticSupport")

from config.db import get_conn
from main import handle_chat_message

def verify_storage():
    session_id = f"test-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    user_id = "verify_user_001"
    user_name = "Verification User"
    user_email = "verify@example.com"
    user_team = "QA Team"
    user_metadata = {"test_run": True, "source": "verify_script"}

    print(f"--- Step 1: Sending message for {user_id} ---")
    response = handle_chat_message(
        message="Hello, this is a storage verification test.",
        session_id=session_id,
        user_id=user_id,
        user_name=user_name,
        user_email=user_email,
        user_team=user_team,
        user_metadata=user_metadata
    )
    print(f"Agent response: {response[:50]}...")

    print(f"\n--- Step 2: Checking user_registry table ---")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, user_name, user_email, user_team, metadata FROM user_registry WHERE user_id = %s",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                print("FAIL: User not found in user_registry")
                return False
            
            db_id, db_name, db_email, db_team, db_meta = row
            print(f"Found user: {db_id}")
            print(f"Name: {db_name} (Expected: {user_name})")
            print(f"Email: {db_email} (Expected: {user_email})")
            print(f"Team: {db_team} (Expected: {user_team})")
            print(f"Metadata: {db_meta}")

            if db_name == user_name and db_email == user_email and db_team == user_team:
                print("\nSUCCESS: User details correctly persisted!")
            else:
                print("\nFAIL: User details mismatch!")
                return False

        print(f"\n--- Step 3: Verifying persistence in ConversationState ---")
        # Load the state in a clean call (without passing user details)
        # It should fetch them from user_registry
        print("Sending follow-up message without passing user details...")
        response2 = handle_chat_message(
            message="Check my profile again.",
            session_id=session_id,
            user_id=user_id
        )
        
        # Check if ConversationState (internal cache/db) still has it
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state_data FROM conversation_state WHERE conversation_id = %s",
                (session_id,)
            )
            row = cur.fetchone()
            if row:
                state_data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                user_record = state_data.get("user") or {}
                print(f"ConversationState user data: {user_record}")
                if user_record.get("user_name") == user_name:
                    print("SUCCESS: ConversationState correctly loaded user details!")
                else:
                    print("FAIL: ConversationState missing user details!")
                    return False
            else:
                print("FAIL: ConversationState not found!")
                return False

    return True

if __name__ == "__main__":
    success = verify_storage()
    sys.exit(0 if success else 1)
