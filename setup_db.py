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
-- active_issue_id is also written by state/issue_tracker.py
CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id  VARCHAR(256) PRIMARY KEY,
    user_id          VARCHAR(256),
    user_role        VARCHAR(32) DEFAULT 'technical',
    phase            VARCHAR(32) DEFAULT 'idle',
    state_data       JSONB DEFAULT '{}'::jsonb,
    active_issue_id  VARCHAR(64),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_state_updated
    ON conversation_state(updated_at);

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
    conn.close()

    print("Database setup complete.")
    print("Tables created:")
    print(f"  - rag_documents (RAG vector store, vector({embed_dim}))")
    print("  - issue_registry (issue tracking)")
    print("  - conversation_state (session persistence + active issue pointer)")
    print()
    print("Next steps:")
    print("  1. Index KB data:  python -m rag.index_all")
    print("  2. Run tests:      python -m pytest tests/test_scenarios.py -v")
    print("  3. Start agent:    python main.py")


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

        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'conversation_state' "
            "AND column_name = 'active_issue_id'"
        )
        if not cur.fetchone():
            cur.execute(
                "ALTER TABLE conversation_state "
                "ADD COLUMN active_issue_id VARCHAR(64)"
            )
            print("  Added active_issue_id column to conversation_state.")

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
