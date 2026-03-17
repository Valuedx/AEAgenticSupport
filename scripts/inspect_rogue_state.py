
import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor

# Force UTF-8 for stdout
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DSN = "postgresql://postgres:root@localhost:5432/ops_agent"

def inspect_state():
    try:
        conn = psycopg2.connect(DSN)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        target = "Start Date"
        print(f"--- Inspecting Conversation States containing '{target}' ---\n")
        
        cur.execute("SELECT conversation_id, state_data FROM conversation_state WHERE state_data::text ILIKE %s ORDER BY updated_at DESC LIMIT 3", (f'%{target}%',))
        hits = cur.fetchall()
        
        for hit in hits:
            cid = hit['conversation_id']
            state = hit['state_data']
            print(f"=== CID: {cid} ===")
            
            # Check for param_collection
            pc = state.get('param_collection')
            if pc:
                print("param_collection:")
                print(json.dumps(pc, indent=2))
            
            # Check for messages that might have these strings
            messages = state.get('messages', [])
            for msg in messages:
                content = str(msg.get('content', ''))
                if target in content:
                    print(f"Found in message ({msg.get('role')}): {content[:200]}...")
            
            print("-" * 40)

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_state()
