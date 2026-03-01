"""
Shared PostgreSQL connection pool.

All database operations should use ``get_conn()`` from this module
instead of calling ``psycopg2.connect()`` directly.  The pool is
created lazily on first use and can be torn down with ``close_pool()``.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.pool

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.db")

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _ensure_pool():
    global _pool
    if _pool is None or _pool.closed:
        dsn = CONFIG["POSTGRES_DSN"]
        maxconn = int(CONFIG.get("DB_POOL_MAX_CONN", 10))
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=maxconn, dsn=dsn,
        )
        logger.info("Connection pool created (max=%d)", maxconn)


@contextmanager
def get_conn():
    """Borrow a connection from the pool; auto-returned on exit."""
    _ensure_pool()
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


@contextmanager
def get_readonly_conn():
    """Borrow a read-only, autocommit connection (for safe SELECT queries)."""
    _ensure_pool()
    conn = _pool.getconn()
    try:
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    finally:
        conn.set_session(readonly=False, autocommit=False)
        _pool.putconn(conn)


def close_pool():
    """Shut down the pool (call on app teardown)."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None
        logger.info("Connection pool closed")
