from __future__ import annotations

from unittest.mock import MagicMock, patch

from config import db


def setup_function():
    db._pool = None
    db._schema_checked = False


def teardown_function():
    db._pool = None
    db._schema_checked = False


@patch("config.db.psycopg2.pool.ThreadedConnectionPool")
def test_get_conn_bootstraps_runtime_schema_once(mock_pool_cls):
    pool = MagicMock()
    pool.closed = False
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    pool.getconn.return_value = conn
    mock_pool_cls.return_value = pool

    with patch.dict(
        db.CONFIG,
        {"POSTGRES_DSN": "postgresql://localhost/test", "DB_POOL_MAX_CONN": 4},
        clear=False,
    ):
        with db.get_conn():
            pass

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        assert any("CREATE TABLE IF NOT EXISTS conversation_state" in sql for sql in executed)
        assert any("CREATE TABLE IF NOT EXISTS chat_messages" in sql for sql in executed)
        assert any("ADD COLUMN IF NOT EXISTS summary" in sql for sql in executed)
        assert conn.commit.called

        cursor.execute.reset_mock()

        with db.get_conn():
            pass

        assert cursor.execute.call_count == 0
