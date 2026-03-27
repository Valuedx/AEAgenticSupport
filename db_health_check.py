"""
Database Health Check Utility (Exhaustive).
Checks table counts, existence, primary key constraints, and last activity.
"""
import os
import sys
import psycopg2
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import CONFIG

def get_db_health():
    dsn = CONFIG["POSTGRES_DSN"]
    print(f"\n============================================================")
    print(f"DATABASE AUDIT REPORT")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"DSN:  {dsn}")
    print(f"============================================================\n")
    
    # Core system tables
    tables = [
        {"name": "conversation_state", "time_col": "updated_at"},
        {"name": "chat_messages", "time_col": "created_at"},
        {"name": "issue_registry", "time_col": "updated_at"},
        {"name": "workflow_catalog", "time_col": "fetched_at"},
        {"name": "rag_documents", "time_col": None},
        {"name": "user_registry", "time_col": "updated_at"},
        {"name": "ticket_registry", "time_col": "created_at"},
        {"name": "tool_execution_log", "time_col": "created_at"},
        {"name": "approval_audit_log", "time_col": "created_at"},
        {"name": "user_feedback", "time_col": "created_at"}
    ]
    
    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            header = f"{'Table Name':<22} | {'Count':<8} | {'PK':<8} | {'Last Activity'}"
            print(header)
            print("-" * len(header))
            
            for table_info in tables:
                table = table_info["name"]
                time_col = table_info["time_col"]
                
                # Check existence
                cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
                if not cur.fetchone():
                    print(f"{table:<22} | {'MISSING':<8} | {'-':<8} | {'N/A'}")
                    continue
                
                # Check count
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                
                # Check PK
                cur.execute("""
                    SELECT count(*)
                    FROM information_schema.table_constraints tc
                    WHERE tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
                """, (table,))
                pk_count = cur.fetchone()[0]
                pk_status = "YES" if pk_count > 0 else "MISSING"
                
                # Check Last Activity
                last_act = "N/A"
                if time_col:
                    try:
                        cur.execute(f"SELECT MAX({time_col}) FROM {table}")
                        max_time = cur.fetchone()[0]
                        if max_time:
                            last_act = max_time.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass
                
                print(f"{table:<22} | {count:<8} | {pk_status:<8} | {last_act}")

            # pgvector check
            print(f"\n============================================================")
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector' AND installed_version IS NOT NULL")
            vector_ext = cur.fetchone()
            print(f"pgvector extension: {'INSTALLED' if vector_ext else 'NOT INSTALLED'}")
            
            # Connection Pool info
            print(f"Configured Max Connections: {CONFIG.get('DB_POOL_MAX_CONN', 10)}")
            print(f"============================================================\n")
            
        conn.close()
    except Exception as e:
        print(f"\n[ERROR] Audit failed: {e}")

if __name__ == "__main__":
    get_db_health()
