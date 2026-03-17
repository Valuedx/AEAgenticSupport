
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.db import get_conn

def search_db_thoroughly():
    queries = ["Start Date", "Any other relevant information"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            tables = [row[0] for row in cur.fetchall()]
            
            for table in tables:
                print(f"Checking table: {table}")
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
                cols = [row[0] for row in cur.fetchall()]
                
                for q in queries:
                    for col in cols:
                        try:
                            # Use ::text to handle id/int/jsonb types
                            cur.execute(f"SELECT count(*) FROM \"{table}\" WHERE \"{col}\"::text ILIKE %s", (f"%{q}%",))
                            count = cur.fetchone()[0]
                            if count > 0:
                                print(f"Found {count} matches for '{q}' in {table}.{col}!")
                                cur.execute(f"SELECT \"{col}\"::text FROM \"{table}\" WHERE \"{col}\"::text ILIKE %s LIMIT 1", (f"%{q}%",))
                                print(f"Sample: {cur.fetchone()[0][:200]}...")
                        except Exception as e:
                            pass # Some columns might not be castable or searchable

if __name__ == "__main__":
    search_db_thoroughly()
