
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.db import get_conn

def search_sop_catalog():
    with get_conn() as conn:
        with conn.cursor() as cur:
            print("--- Searching in sop_catalog ---")
            cur.execute("""
                SELECT sop_id, title, content 
                FROM sop_catalog 
                WHERE content ILIKE '%Mandatory%' 
                   OR content ILIKE '%relevant information%'
                   OR content ILIKE '%Start Date%'
            """)
            rows = cur.fetchall()
            print(f"Found {len(rows)} matches in sop_catalog:")
            for row in rows:
                print(f"ID: {row[0]}")
                print(f"Title: {row[1]}")
                print(f"Content: {row[2]}")
                print("-" * 20)

if __name__ == "__main__":
    search_sop_catalog()
