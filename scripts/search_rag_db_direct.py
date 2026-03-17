
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.db import get_conn

def find_in_rag_docs():
    queries = ["Start Date", "Any other relevant information"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for q in queries:
                print(f"\n=== Searching for '{q}' in rag_documents ===")
                cur.execute("""
                    SELECT id, collection, content, metadata 
                    FROM rag_documents 
                    WHERE content ILIKE %s
                """, (f"%{q}%",))
                rows = cur.fetchall()
                print(f"Found {len(rows)} matches:")
                for row in rows:
                    print(f"ID: {row[0]}")
                    print(f"Collection: {row[1]}")
                    print(f"Metadata: {json.dumps(row[3], indent=2)}")
                    print(f"Content:\n{row[2]}")
                    print("-" * 30)

if __name__ == "__main__":
    find_in_rag_docs()
