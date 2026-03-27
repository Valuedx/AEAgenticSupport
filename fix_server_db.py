"""
One-shot script to fix missing Primary Keys and broken SERIAL sequences
on the server database. Uses the project's .env config to connect.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import psycopg2
from config.settings import CONFIG

# Tables that need PK enforcement
PK_FIXES = [
    {"table": "ticket_registry", "pk_cols": "ticket_id"},
    {"table": "tool_execution_log", "pk_cols": "id"},
]

# Tables that need SERIAL sequence (auto-increment) repair
SERIAL_FIXES = [
    {"table": "chat_messages", "col": "id"},
    {"table": "tool_execution_log", "col": "id"},
    {"table": "user_feedback", "col": "id"},
    {"table": "approval_audit_log", "col": "id"},
]

def fix_all():
    dsn = CONFIG["POSTGRES_DSN"]
    print(f"Connecting to: {dsn.split('@')[1] if '@' in dsn else dsn}")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    with conn.cursor() as cur:
        # ── Step 1: Fix Missing Primary Keys ──
        print("\n=== STEP 1: Fix Missing Primary Keys ===")
        for fix in PK_FIXES:
            table = fix["table"]
            pk_cols = fix["pk_cols"]

            cur.execute("""
                SELECT 1 FROM information_schema.table_constraints 
                WHERE table_name = %s AND constraint_type = 'PRIMARY KEY'
            """, (table,))

            if cur.fetchone():
                print(f"  [{table}] PK already exists. SKIP.")
                continue

            print(f"  [{table}] PK MISSING. Fixing...")
            cols = [c.strip() for c in pk_cols.split(",")]
            dup_cond = " AND ".join([f"a.{c} = b.{c}" for c in cols])
            try:
                cur.execute(f"DELETE FROM {table} a USING {table} b WHERE a.ctid < b.ctid AND {dup_cond}")
                print(f"    Cleaned duplicates.")
            except Exception as e:
                print(f"    Dup cleanup warning: {e}")

            try:
                cur.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({pk_cols})")
                print(f"    SUCCESS: PK ({pk_cols}) added.")
            except Exception as e:
                print(f"    FAILED: {e}")

        # ── Step 2: Fix SERIAL auto-increment sequences ──
        print("\n=== STEP 2: Fix SERIAL Auto-Increment Sequences ===")
        for fix in SERIAL_FIXES:
            table = fix["table"]
            col = fix["col"]
            seq_name = f"{table}_{col}_seq"

            try:
                # Check if sequence exists
                cur.execute("SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'S'", (seq_name,))
                if not cur.fetchone():
                    # Create the sequence
                    cur.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq_name}")
                    print(f"  [{table}] Created sequence {seq_name}")

                # Get current max id
                cur.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {table}")
                max_id = cur.fetchone()[0]

                # Set sequence to max + 1
                cur.execute(f"SELECT setval('{seq_name}', GREATEST({max_id}, 1))")
                print(f"  [{table}] Sequence set to {max(max_id, 1)}")

                # Set column default to use the sequence
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT nextval('{seq_name}')")
                print(f"  [{table}] Column default set to nextval('{seq_name}')")

                # Make column NOT NULL
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET NOT NULL")
                print(f"  [{table}] Column {col} set to NOT NULL")

            except Exception as e:
                print(f"  [{table}] Error: {e}")

        # ── Step 3: Full Verification ──
        print("\n=== STEP 3: VERIFICATION ===")
        all_tables = [
            "conversation_state", "chat_messages", "issue_registry",
            "workflow_catalog", "rag_documents", "user_registry",
            "ticket_registry", "tool_execution_log", "approval_audit_log",
            "user_feedback"
        ]
        print(f"{'Table':<25} | {'Count':<8} | {'PK':<8} | {'Serial ID'}")
        print("-" * 65)
        for t in all_tables:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (t,))
            if not cur.fetchone():
                print(f"{t:<25} | {'MISSING':<8} | {'-':<8} | -")
                continue
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            count = cur.fetchone()[0]
            cur.execute("""
                SELECT 1 FROM information_schema.table_constraints 
                WHERE table_name = %s AND constraint_type = 'PRIMARY KEY'
            """, (t,))
            pk = "YES" if cur.fetchone() else "MISSING"

            # Check if id column has a default (serial)
            serial = "-"
            cur.execute("""
                SELECT column_default FROM information_schema.columns 
                WHERE table_name = %s AND column_name = 'id'
            """, (t,))
            row = cur.fetchone()
            if row and row[0]:
                serial = "YES" if "nextval" in str(row[0]) else "NO"

            print(f"{t:<25} | {count:<8} | {pk:<8} | {serial}")

    conn.close()
    print("\nDone. All fixes applied.")

if __name__ == "__main__":
    fix_all()
