"""
Tests for Feature 2.4: Human-in-the-loop (HITL) Approval.
"""
import pytest
from unittest.mock import MagicMock, patch
from agents.approval_gate import ApprovalGate, ApprovalRequest

@pytest.fixture
def mock_db():
    with patch('agents.approval_gate.get_conn') as mock:
        yield mock

class TestApprovalGate:
    def test_create_approval_request_persistence(self, mock_db):
        gate = ApprovalGate()
        request = gate.create_approval_request(
            conversation_id="test-conv-1",
            tool_name="restart_service",
            tier="medium_risk",
            params={"service": "nginx"},
            summary="Restarting nginx"
        )
        
        # Verify db insert was called
        conn = mock_db.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        assert any("INSERT INTO approval_audit_log" in str(call) for call in cur.execute.call_args_list)

    def test_log_decision(self, mock_db):
        gate = ApprovalGate()
        gate.log_decision("test-conv-1", "APPROVED", "admin-user")
        
        conn = mock_db.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        assert any("UPDATE approval_audit_log" in str(call) for call in cur.execute.call_args_list)
        assert "APPROVED" in str(cur.execute.call_args_list[0])

if __name__ == "__main__":
    pytest.main([__file__])
