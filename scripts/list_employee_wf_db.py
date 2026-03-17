
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.db import get_conn

def check_schema():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'workflow_catalog'
            """)
            rows = cur.fetchall()
            print("Columns in workflow_catalog:")
            for row in rows:
                print(f"  - {row[0]} ({row[1]})")

            print("\n--- Listing All Employee-related Workflows ---")
            cur.execute("""
                SELECT workflow_name, parameters, description 
                FROM workflow_catalog 
                WHERE workflow_name ILIKE '%employee%'
            """)
            rows = cur.fetchall()
            for row in rows:
                print(f"Workflow: {row[0]}")
                print(f"Params: {json.dumps(row[1], indent=2)}")
                print("-" * 20)

if __name__ == "__main__":
    check_schema()
