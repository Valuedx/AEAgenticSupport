
import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from config.db import get_readonly_conn

def list_all_workflows():
    with get_readonly_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT workflow_name, workflow_id FROM workflow_catalog")
            results = cur.fetchall()
            
            for name, wid in results:
                print(f"Name: {name} (ID: {wid})")

if __name__ == "__main__":
    list_all_workflows()
