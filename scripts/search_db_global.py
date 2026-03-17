
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.db import get_conn

def list_tables_and_search():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            tables = [row[0] for row in cur.fetchall()]
            print(f"Public tables: {tables}")
            
            for table in tables:
                print(f"\n--- Searching in table: {table} ---")
                try:
                    # Check if table has text/jsonb columns to search
                    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}' AND (data_type LIKE '%text%' OR data_type LIKE '%json%')")
                    cols = [row[0] for row in cur.fetchall()]
                    if not cols:
                        continue
                        
                    where_clause = " OR ".join([f"{col}::text ILIKE '%Mandatory%'" for col in cols])
                    cur.execute(f"SELECT * FROM {table} WHERE {where_clause} LIMIT 5")
                    rows = cur.fetchall()
                    if rows:
                        print(f"Found matches in {table}!")
                        for row in rows:
                            print(row)
                except Exception as e:
                    print(f"Error searching {table}: {e}")

if __name__ == "__main__":
    list_tables_and_search()
