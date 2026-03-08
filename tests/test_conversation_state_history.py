from __future__ import annotations

from datetime import datetime, timezone

import state.conversation_state as conversation_state_module
from state.conversation_state import ConversationState


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self._rows)


def test_search_conversations_returns_aggregated_rows(monkeypatch):
    now = datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc)
    rows = [
        (
            "conv-123",
            "alice",
            "business",
            "resolved",
            "Payment batch issue resolved",
            True,
            now,
            8,
            "The latest assistant reply",
            now,
            "payment batch",
        )
    ]
    monkeypatch.setattr(conversation_state_module, "get_conn", lambda: _FakeConn(rows))

    results = ConversationState.search_conversations("payment", limit=5)

    assert results == [
        {
            "conversation_id": "conv-123",
            "user_id": "alice",
            "user_role": "business",
            "phase": "resolved",
            "summary": "Payment batch issue resolved",
            "is_human_handoff": True,
            "updated_at": now.isoformat(),
            "message_count": 8,
            "last_message": "The latest assistant reply",
            "last_message_at": now.isoformat(),
            "match_excerpt": "payment batch",
        }
    ]
