"""
Database setup script.
Creates all PostgreSQL tables and extensions required by the ops agent.
Run once during initial deployment:
    python setup_db.py
"""

import os
import sys
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import CONFIG

SCHEMA_SQL = """
-- pgvector extension for RAG
CREATE EXTENSION IF NOT EXISTS vector;

-- RAG documents (created by rag/engine.py but included here for completeness)
CREATE TABLE IF NOT EXISTS rag_documents (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    collection  TEXT NOT NULL,
    embedding   vector(384),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_collection
    ON rag_documents (collection);

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

CREATE TABLE IF NOT EXISTS issue_tracker_state (
    conversation_id  VARCHAR(256) PRIMARY KEY,
    active_issue_id  VARCHAR(64),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Conversation state persistence (used by state/conversation_state.py)
CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id  VARCHAR(256) PRIMARY KEY,
    user_id          VARCHAR(256),
    user_role        VARCHAR(32) DEFAULT 'technical',
    phase            VARCHAR(32) DEFAULT 'idle',
    state_data       JSONB DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_state_updated
    ON conversation_state(updated_at);
"""


def setup_database():
    dsn = CONFIG["POSTGRES_DSN"]
    print(f"Connecting to: {dsn}")

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            for statement in SCHEMA_SQL.split(";"):
                statement = statement.strip()
                if statement and not statement.startswith("--"):
                    try:
                        cur.execute(statement + ";")
                    except psycopg2.Error as e:
                        print(f"  Warning: {e.pgerror or e}")
        conn.commit()

    print("Database setup complete.")
    print("Tables created:")
    print("  - rag_documents (RAG vector store)")
    print("  - issue_registry (issue tracking)")
    print("  - issue_tracker_state (active issue pointer)")
    print("  - conversation_state (session persistence)")
    print()
    print("Next steps:")
    print("  1. Index KB data:  python -m rag.index_all")
    print("  2. Run tests:      python -m pytest tests/test_scenarios.py -v")
    print("  3. Start agent:    python main.py")


if __name__ == "__main__":
    setup_database()
