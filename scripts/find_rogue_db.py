
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.db import get_conn

def find_rogue_strings():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Search in parameters (JSONB) and description
            cur.execute("""
                SELECT workflow_name, parameters, description 
                FROM workflow_catalog 
                WHERE description ILIKE '%Start Date%' 
                   OR description ILIKE '%Any other relevant information%'
                   OR parameters::text ILIKE '%Start Date%'
                   OR parameters::text ILIKE '%Any other relevant information%'
            """)
            rows = cur.fetchall()
            print(f"Found {len(rows)} workflows matching rogue strings:")
            for row in rows:
                print(f"Workflow: {row[0]}")
                print(f"Description: {row[2]}")
                # print(f"Params: {json.dumps(row[1], indent=2)}")
                print("-" * 20)

if __name__ == "__main__":
    find_rogue_strings()
