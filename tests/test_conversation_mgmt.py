"""
Tests for Feature 2.3: Conversation & Session Management.
"""
import pytest
from unittest.mock import MagicMock, patch
from state.conversation_state import ConversationState, ConversationPhase
from state.session_manager import SessionManager

@pytest.fixture
def mock_db():
    with patch('state.session_manager.get_conn') as mock:
        yield mock

@pytest.fixture
def mock_db_state():
    with patch('state.conversation_state.get_conn') as mock:
        yield mock

class TestConversationManagement:
    def test_add_message_persistence(self, mock_db_state):
        state = ConversationState()
        state.conversation_id = "test-123"
        state.add_message("user", "hello world")
        
        # Verify db insert was called
        conn = mock_db_state.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        assert cur.execute.called

    def test_export_markdown(self):
        state = ConversationState()
        state.conversation_id = "test-123"
        state.add_message("user", "Hello")
        state.add_message("assistant", "Hi there")
        state.summary = "A friendly greeting"
        
        md = state.export_history(format="markdown")
        assert "# Conversation Export: test-123" in md
        assert "**Summary:** A friendly greeting" in md
        assert "**USER**" in md
        assert "Hello" in md

    def test_feedback_persistence(self, mock_db_state):
        state = ConversationState()
        state.conversation_id = "test-123"
        state.save_feedback(5, "Great job!")
        
        conn = mock_db_state.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        assert any("INSERT INTO user_feedback" in str(call) for call in cur.execute.call_args_list)

    def test_session_cleanup(self, mock_db):
        conn = mock_db.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [("old-conv-1",)]
        
        count = SessionManager.cleanup_stale_sessions(max_age_days=30)
        assert count == 1
        assert any("DELETE FROM conversation_state" in str(call) for call in cur.execute.call_args_list)

if __name__ == "__main__":
    pytest.main([__file__])
