
import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor

# Force UTF-8 for stdout
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DSN = "postgresql://postgres:root@localhost:5432/ops_agent"

def search_postgres():
    try:
        conn = psycopg2.connect(DSN)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        targets = ["Start Date", "Any other relevant information"]
        print(f"--- Searching PostgreSQL for {targets} ---\n")
        
        # 1. Check workflow_catalog
        print("Checking table: workflow_catalog")
        cur.execute("SELECT workflow_name, description, parameters FROM workflow_catalog WHERE workflow_name = 'WF_add_employee'")
        row = cur.fetchone()
        if row:
            print(f"Workflow: {row['workflow_name']}")
            print(f"Description: {row['description']}")
            print("Parameters:")
            print(json.dumps(row['parameters'], indent=2))
        else:
            print("WF_add_employee not found in workflow_catalog.")
            
        # 2. Check rag_documents (collections: tools, sops)
        print("\nChecking table: rag_documents (Tools and SOPs)")
        for target in targets:
            cur.execute("SELECT id, collection, content, metadata FROM rag_documents WHERE content ILIKE %s OR metadata::text ILIKE %s", (f'%{target}%', f'%{target}%'))
            hits = cur.fetchall()
            if hits:
                print(f"Found {len(hits)} matches for '{target}' in rag_documents:")
                for hit in hits:
                    print(f"- ID: {hit['id']} (Collection: {hit['collection']})")
                    print(f"  Content Snippet: {str(hit['content'])[:150]}...")
            else:
                print(f"No matches for '{target}' in rag_documents.")

        # 3. Check conversation_state
        print("\nChecking table: conversation_state")
        for target in targets:
            cur.execute("SELECT conversation_id, state_data FROM conversation_state WHERE state_data::text ILIKE %s", (f'%{target}%',))
            hits = cur.fetchall()
            if hits:
                print(f"Found {len(hits)} matches for '{target}' in conversation_state.")
            else:
                 print(f"No matches for '{target}' in conversation_state.")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    search_postgres()
