"""
Database setup script.
Creates all PostgreSQL tables and extensions required by the ops agent.
Run once during initial deployment:
    python setup_db.py
"""
from __future__ import annotations

import os
import sys
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import CONFIG


def _has_pgvector(dsn: str) -> bool:
    try:
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def _get_embedding_dimension() -> int:
    """Derive vector dimension from the configured Vertex AI embedding model."""
    try:
        from google import genai
        from google.genai import types
        
        client = genai.Client(
            vertexai=True,
            project=CONFIG["GOOGLE_CLOUD_PROJECT"],
            location=CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        )
        model_name = CONFIG.get("EMBEDDING_MODEL", "text-embedding-004")
        res = client.models.embed_content(
            model=model_name,
            contents=["dimension probe"],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
        )
        dim = len(res.embeddings[0].values)
        print(f"  Detected embedding dimension {dim} from model '{model_name}'")
        return dim
    except Exception as e:
        print(f"  Could not query embedding model ({e}) — defaulting to 768")
        return 768


def _build_schema_sql(embed_dim: int, use_pgvector: bool) -> str:
    if use_pgvector:
        rag_block = f"""
-- pgvector extension for RAG
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{{}}'::jsonb,
    collection  TEXT NOT NULL,
    embedding   vector({embed_dim}),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_collection
    ON rag_documents (collection);"""
    else:
        rag_block = """
-- RAG documents (numpy fallback — embeddings stored as JSONB arrays)
CREATE TABLE IF NOT EXISTS rag_documents (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    collection  TEXT NOT NULL,
    embedding   JSONB DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_collection
    ON rag_documents (collection);"""

    return rag_block + """

-- Issue tracking (used by state/issue_tracker.py)
CREATE TABLE IF NOT EXISTS issue_registry (
    conversation_id VARCHAR(256) NOT NULL,
    issue_id        VARCHAR(64)  NOT NULL,
    issue_data      JSONB        NOT NULL,
    updated_at      TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (conversation_id, issue_id)
);

CREATE INDEX IF NOT EXISTS idx_issue_registry_conv
    ON issue_registry(conversation_id);

-- Conversation state persistence (used by state/conversation_state.py)
CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id  VARCHAR(256) PRIMARY KEY,
    user_id          VARCHAR(256),
    user_role        VARCHAR(32) DEFAULT 'technical',
    phase            VARCHAR(32) DEFAULT 'idle',
    state_data       JSONB DEFAULT '{}'::jsonb,
    active_issue_id  VARCHAR(64),
    summary          TEXT,                  -- Feature 2.3.6
    is_human_handoff BOOLEAN DEFAULT FALSE, -- Feature 2.3.4
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_state_updated
    ON conversation_state(updated_at);

-- FEATURE 2.3.1: Global message store for cross-session history search
CREATE TABLE IF NOT EXISTS chat_messages (
    id             SERIAL PRIMARY KEY,
    conversation_id VARCHAR(256) NOT NULL,
    role           VARCHAR(32) NOT NULL,
    content        TEXT NOT NULL,
    metadata       JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
    ON chat_messages(conversation_id);

-- FEATURE 2.3.5: User feedback tracking
CREATE TABLE IF NOT EXISTS user_feedback (
    id             SERIAL PRIMARY KEY,
    conversation_id VARCHAR(256) NOT NULL UNIQUE,
    user_id        VARCHAR(256),
    rating         INTEGER CHECK (rating >= 1 AND rating <= 5),
    comments       TEXT,
    metadata       JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_feedback_conv
    ON user_feedback(conversation_id);

-- FEATURE 2.4.2: Audit log for human-in-the-loop approvals
CREATE TABLE IF NOT EXISTS approval_audit_log (
    id              SERIAL PRIMARY KEY,
    conversation_id VARCHAR(256) NOT NULL,
    request_id      VARCHAR(64)  NOT NULL,
    tool_name       VARCHAR(256) NOT NULL,
    tool_params     JSONB,
    requester_role  VARCHAR(32),
    approver_id     VARCHAR(256),
    status          VARCHAR(32),  -- PENDING, APPROVED, REJECTED, CANCELLED
    tier            VARCHAR(32),
    summary         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    decided_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approval_audit_conv 
    ON approval_audit_log(conversation_id);

-- Migrations for existing deployments
ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS is_human_handoff BOOLEAN DEFAULT FALSE;

-- Tool execution audit log (Postgres-backed, survives restarts)
-- Mirrors agent_catalog.json interactions but with full params + result
CREATE TABLE IF NOT EXISTS tool_execution_log (
    id              BIGSERIAL    PRIMARY KEY,
    conversation_id VARCHAR(256) NOT NULL DEFAULT '',
    agent_id        VARCHAR(128) NOT NULL DEFAULT 'unmapped',
    tool_name       VARCHAR(256) NOT NULL,
    params          JSONB        DEFAULT '{}'::jsonb,
    result          JSONB        DEFAULT '{}'::jsonb,
    success         BOOLEAN      NOT NULL DEFAULT FALSE,
    error_message   TEXT         DEFAULT '',
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_exec_log_tool
    ON tool_execution_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_exec_log_conv
    ON tool_execution_log(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tool_exec_log_created
    ON tool_execution_log(created_at);

-- Workflow catalog cache (T4 API workflow list, refreshed on startup/reload)
-- Avoids repeated expensive T4 API calls for RAG tool discovery
CREATE TABLE IF NOT EXISTS workflow_catalog (
    workflow_id     VARCHAR(64)  NOT NULL,
    org_code        VARCHAR(64)  NOT NULL DEFAULT '',
    workflow_name   VARCHAR(512) NOT NULL,
    description     TEXT         DEFAULT '',
    category        VARCHAR(128) DEFAULT '',
    active          BOOLEAN      DEFAULT TRUE,
    parameters      JSONB        DEFAULT '[]'::jsonb,
    raw_data        JSONB        DEFAULT '{}'::jsonb,
    fetched_at      TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (workflow_id, org_code)
);

CREATE INDEX IF NOT EXISTS idx_workflow_catalog_name
    ON workflow_catalog(workflow_name);
CREATE INDEX IF NOT EXISTS idx_workflow_catalog_active
    ON workflow_catalog(active);

-- HDFC Ticket Lifecycle Registry
CREATE TABLE IF NOT EXISTS ticket_registry (
    id               BIGSERIAL PRIMARY KEY,
    ticket_id        VARCHAR(64)   NOT NULL UNIQUE,
    process_name     VARCHAR(256)  NOT NULL,
    description      TEXT          NOT NULL,
    request_type     VARCHAR(32)   DEFAULT 'Request',
    status           VARCHAR(32)   DEFAULT 'OPEN',
    environment      VARCHAR(16)   DEFAULT 'uat',
    user_id          VARCHAR(256),
    conversation_id  VARCHAR(256),
    workflow_name    VARCHAR(256),
    created_at       TIMESTAMPTZ   DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   DEFAULT NOW(),
    closed_at        TIMESTAMPTZ,
    resolution_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_ticket_registry_tid ON ticket_registry(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_registry_user ON ticket_registry(user_id);

-- User Registry (used by state/conversation_state.py)
CREATE TABLE IF NOT EXISTS user_registry (
    user_id     VARCHAR(256) PRIMARY KEY,
    user_role   VARCHAR(32) DEFAULT 'technical',
    user_name   VARCHAR(256),
    user_email  VARCHAR(256),
    user_team   VARCHAR(256),
    metadata    JSONB DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""


def setup_database():
    dsn = CONFIG["POSTGRES_DSN"]
    print(f"Connecting to: {dsn}")

    use_pgvector = _has_pgvector(dsn)
    if use_pgvector:
        print("  pgvector extension available — using native vector columns")
    else:
        print("  pgvector not available — using JSONB fallback for embeddings")

    embed_dim = _get_embedding_dimension()
    schema_sql = _build_schema_sql(embed_dim, use_pgvector)

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Step 1: Run standard CREATE TABLE IF NOT EXISTS
        for statement in schema_sql.split(";"):
            lines = [
                ln for ln in statement.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            clean = "\n".join(lines).strip()
            if not clean:
                continue
            try:
                cur.execute(clean + ";")
            except psycopg2.Error as e:
                print(f"  Warning: {e.pgerror or e}")

        # Step 2: Enforce Primary Keys and Missing Columns (Self-Healing Migrations)
        print("  Verifying schema constraints...")
        
        # Helper to ensure PK
        def ensure_pk(table_name, pk_cols):
            cur.execute(f"SELECT 1 FROM information_schema.table_constraints WHERE table_name = '{table_name}' AND constraint_type = 'PRIMARY KEY'")
            if not cur.fetchone():
                print(f"  Fixing: Missing PRIMARY KEY on {table_name}")
                try:
                    cur.execute(f"ALTER TABLE {table_name} ADD PRIMARY KEY ({pk_cols})")
                except Exception as e:
                    print(f"    Error on {table_name}: {e}")
                    # Try to remove duplicates first
                    print(f"    Searching for duplicates in {table_name}...")
                    cur.execute(f"DELETE FROM {table_name} a USING {table_name} b WHERE a.ctid < b.ctid AND " + " AND ".join([f"a.{c} = b.{c}" for c in pk_cols.split(",")]))
                    try:
                        cur.execute(f"ALTER TABLE {table_name} ADD PRIMARY KEY ({pk_cols})")
                        print(f"    Success: PK added to {table_name}")
                    except Exception as e2:
                        print(f"    Final Failure for {table_name}: {e2}")

        ensure_pk("conversation_state", "conversation_id")
        ensure_pk("workflow_catalog", "workflow_id, org_code")
        ensure_pk("rag_documents", "id")
        ensure_pk("issue_registry", "conversation_id, issue_id")
        ensure_pk("ticket_registry", "ticket_id")
        ensure_pk("chat_messages", "id")
        ensure_pk("user_feedback", "id")
        ensure_pk("approval_audit_log", "id")
        ensure_pk("tool_execution_log", "id")
        ensure_pk("user_registry", "user_id")
        
        # Ensure critical columns in conversation_state
        cur.execute("ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS summary TEXT")
        cur.execute("ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS is_human_handoff BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS active_issue_id VARCHAR(64)")

        # Ensure tsv in rag_documents
        if use_pgvector:
            cur.execute("ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS tsv tsvector")
        else:
            cur.execute("ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS tsv TEXT")

    conn.close()

    print("Database setup complete.")
    print("Tables verified and constraints enforced.")
    print("Run 'python db_health_check.py' to confirm status.")

def migrate_from_issue_tracker_state():
    """One-time migration: move active_issue_id data from the old
    ``issue_tracker_state`` table into ``conversation_state``, then
    drop the old table.  Safe to run multiple times.
    """
    dsn = CONFIG["POSTGRES_DSN"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'issue_tracker_state'"
        )
        if not cur.fetchone():
            print("  issue_tracker_state does not exist — nothing to migrate.")
            conn.close()
            return

        cur.execute("""
            UPDATE conversation_state cs
            SET active_issue_id = its.active_issue_id
            FROM issue_tracker_state its
            WHERE cs.conversation_id = its.conversation_id
              AND its.active_issue_id IS NOT NULL
        """)
        print(f"  Migrated {cur.rowcount} active_issue_id values.")

        cur.execute("DROP TABLE issue_tracker_state")
        print("  Dropped issue_tracker_state table.")
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--migrate":
        migrate_from_issue_tracker_state()
    else:
        setup_database()
