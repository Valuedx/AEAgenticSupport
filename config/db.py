"""
Shared PostgreSQL connection pool.

All database operations should use ``get_conn()`` from this module
instead of calling ``psycopg2.connect()`` directly.  The pool is
created lazily on first use and can be torn down with ``close_pool()``.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.pool

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.db")

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_schema_checked = False
_schema_lock = threading.Lock()

_RUNTIME_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS issue_registry (
        conversation_id VARCHAR(256) NOT NULL,
        issue_id        VARCHAR(64)  NOT NULL,
        issue_data      JSONB        NOT NULL,
        updated_at      TIMESTAMPTZ  DEFAULT NOW(),
        PRIMARY KEY (conversation_id, issue_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_issue_registry_conv
        ON issue_registry(conversation_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_state (
        conversation_id  VARCHAR(256) PRIMARY KEY,
        user_id          VARCHAR(256),
        user_role        VARCHAR(32) DEFAULT 'technical',
        phase            VARCHAR(32) DEFAULT 'idle',
        state_data       JSONB DEFAULT '{}'::jsonb,
        active_issue_id  VARCHAR(64),
        summary          TEXT,
        is_human_handoff BOOLEAN DEFAULT FALSE,
        updated_at       TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_state_updated
        ON conversation_state(updated_at)
    """,
    """
    ALTER TABLE conversation_state
        ADD COLUMN IF NOT EXISTS active_issue_id VARCHAR(64)
    """,
    """
    ALTER TABLE conversation_state
        ADD COLUMN IF NOT EXISTS summary TEXT
    """,
    """
    ALTER TABLE conversation_state
        ADD COLUMN IF NOT EXISTS is_human_handoff BOOLEAN DEFAULT FALSE
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id              SERIAL PRIMARY KEY,
        conversation_id VARCHAR(256) NOT NULL,
        role            VARCHAR(32) NOT NULL,
        content         TEXT NOT NULL,
        metadata        JSONB DEFAULT '{}'::jsonb,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
        ON chat_messages(conversation_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS user_feedback (
        id              SERIAL PRIMARY KEY,
        conversation_id VARCHAR(256) NOT NULL UNIQUE,
        user_id         VARCHAR(256),
        rating          INTEGER CHECK (rating >= 1 AND rating <= 5),
        comments        TEXT,
        metadata        JSONB DEFAULT '{}'::jsonb,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_user_feedback_conv
        ON user_feedback(conversation_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_audit_log (
        id              SERIAL PRIMARY KEY,
        conversation_id VARCHAR(256) NOT NULL,
        request_id      VARCHAR(64)  NOT NULL,
        tool_name       VARCHAR(256) NOT NULL,
        tool_params     JSONB,
        requester_role  VARCHAR(32),
        approver_id     VARCHAR(256),
        status          VARCHAR(32),
        tier            VARCHAR(32),
        summary         TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        decided_at      TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_approval_audit_conv
        ON approval_audit_log(conversation_id)
    """,
    """
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
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_exec_log_tool
        ON tool_execution_log(tool_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_exec_log_conv
        ON tool_execution_log(conversation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_exec_log_created
        ON tool_execution_log(created_at)
    """,
    """
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
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workflow_catalog_name
        ON workflow_catalog(workflow_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workflow_catalog_active
        ON workflow_catalog(active)
    """,
    """
    CREATE TABLE IF NOT EXISTS user_registry (
        user_id         VARCHAR(256) PRIMARY KEY,
        user_role       VARCHAR(32)  DEFAULT 'technical',
        user_name       VARCHAR(256),
        user_email      VARCHAR(256),
        user_team       VARCHAR(128),
        metadata        JSONB        DEFAULT '{}'::jsonb,
        created_at      TIMESTAMPTZ  DEFAULT NOW(),
        updated_at      TIMESTAMPTZ  DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_user_registry_email
        ON user_registry(user_email)
    """,
)


def _ensure_pool():
    global _pool
    if _pool is None or _pool.closed:
        dsn = CONFIG["POSTGRES_DSN"]
        maxconn = int(CONFIG.get("DB_POOL_MAX_CONN", 10))
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=maxconn, dsn=dsn,
        )
        logger.info("Connection pool created (max=%d)", maxconn)


def _ensure_runtime_schema():
    global _schema_checked
    if _schema_checked:
        return

    with _schema_lock:
        if _schema_checked or _pool is None or _pool.closed:
            return

        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                for statement in _RUNTIME_SCHEMA_STATEMENTS:
                    cur.execute(statement)
            conn.commit()
            _schema_checked = True
            logger.info("Runtime database schema verified.")
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("Runtime schema verification failed: %s", exc)
        finally:
            _pool.putconn(conn)


@contextmanager
def get_conn():
    """Borrow a connection from the pool; auto-returned on exit."""
    _ensure_pool()
    _ensure_runtime_schema()
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


@contextmanager
def get_readonly_conn():
    """Borrow a read-only, autocommit connection (for safe SELECT queries)."""
    _ensure_pool()
    _ensure_runtime_schema()
    conn = _pool.getconn()
    try:
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    finally:
        conn.set_session(readonly=False, autocommit=False)
        _pool.putconn(conn)


def close_pool():
    """Shut down the pool (call on app teardown)."""
    global _pool, _schema_checked
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None
        _schema_checked = False
        logger.info("Connection pool closed")
