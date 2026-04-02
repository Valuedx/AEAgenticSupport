from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom.helpers import rag as rag_helper


@patch("custom.helpers.rag.is_read_enforced", return_value=True)
def test_rag_search_tools_fails_closed_without_user_id(_mock_read_enforced):
    client = MagicMock()

    result = rag_helper.rag_search_tools(client, query="run claims", top_k=5)

    assert result == []
    client.call.assert_not_called()
