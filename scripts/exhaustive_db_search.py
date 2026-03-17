
import os
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor

# Force UTF-8 for stdout
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DSN = "postgresql://postgres:root@localhost:5432/ops_agent"

def global_db_search():
    try:
        conn = psycopg2.connect(DSN)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        target = "Start Date"
        print(f"--- Exhaustive DB Search for '{target}' ---\n")
        
        # Get all textual columns
        cur.execute("""
            SELECT table_name, column_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND data_type IN ('text', 'json', 'jsonb', 'character varying')
        """)
        columns = cur.fetchall()
        
        found_any = False
        for entry in columns:
            table = entry['table_name']
            column = entry['column_name']
            
            try:
                # Use ::text cast for json/jsonb columns
                query = f"SELECT COUNT(*) FROM \"{table}\" WHERE \"{column}\"::text ILIKE %s"
                cur.execute(query, (f'%{target}%',))
                count = cur.fetchone()['count']
                
                if count > 0:
                    print(f"MATCH: {table}.{column} -> {count} records")
                    found_any = True
                    # Fetch a sample
                    sample_query = f"SELECT \"{column}\"::text as content FROM \"{table}\" WHERE \"{column}\"::text ILIKE %s LIMIT 1"
                    cur.execute(sample_query, (f'%{target}%',))
                    sample = cur.fetchone()
                    print(f"  Sample: {sample['content'][:200]}...")
            except Exception as e:
                # Skip tables that might be locked or have issues
                continue

        if not found_any:
            print("No matches found in any table.")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    global_db_search()
