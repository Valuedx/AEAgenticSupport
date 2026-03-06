"""
Tests for Feature 2.6: Security & Compliance (RBAC).
"""
import pytest
from agents.orchestrator import Orchestrator
from state.conversation_state import ConversationState

class TestRBAC:
    def test_rbac_check_sufficient(self):
        orch = Orchestrator()
        state = ConversationState()
        state.user_role = "admin" # Rank 100
        
        ok, err = orch._check_rbac(state, "high_risk") # Rank 50
        assert ok is True
        assert err == ""

    def test_rbac_check_insufficient(self):
        orch = Orchestrator()
        state = ConversationState()
        state.user_role = "support" # Rank 10 (from settings.py)
        
        ok, err = orch._check_rbac(state, "high_risk") # Rank 50
        assert ok is False
        assert "insufficient" in err
        assert "Minimum role required: dev" in err or "admin" in err

    def test_min_role_for_tier(self):
        orch = Orchestrator()
        assert orch._get_min_role_for_tier("read_only") == "readonly"
        assert orch._get_min_role_for_tier("low_risk") == "support"
        assert orch._get_min_role_for_tier("medium_risk") == "dev"
        assert orch._get_min_role_for_tier("high_risk") == "dev" # Since dev rank is 50 and high_risk is 50

if __name__ == "__main__":
    pytest.main([__file__])
