"""
E2E API test — no browser. Verifies agent + mock API work together.
Run: python -m pytest tests/test_e2e_api.py -v
Or:  python tests/test_e2e_api.py
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx


AGENT_URL = "http://localhost:5050"
MOCK_API_URL = "http://localhost:5051"


def test_agent_health():
    r = httpx.get(f"{AGENT_URL}/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"


def test_mock_api_workflow_status():
    """Mock AE API returns 200 for workflow status (used by check_workflow_status)."""
    r = httpx.get(
        f"{MOCK_API_URL}/api/v1/workflows/Policy_Renewal_Batch/status",
        headers={"Authorization": "Bearer mock-local-dev"},
        timeout=10,
    )
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert data.get("workflow_name") == "Policy_Renewal_Batch"


def test_mock_api_incidents():
    """Mock AE API returns 200 for incident creation (create_incident_ticket)."""
    r = httpx.post(
        f"{MOCK_API_URL}/api/v1/incidents",
        headers={
            "Authorization": "Bearer mock-local-dev",
            "Content-Type": "application/json",
        },
        json={"title": "Test", "description": "E2E test", "priority": "P3"},
        timeout=10,
    )
    assert r.status_code == 200
    data = r.json()
    assert "incident_id" in data or "status" in data


def test_chat_quick_action():
    """Agent accepts message and returns a response (tools hit mock API)."""
    r = httpx.post(
        f"{AGENT_URL}/chat",
        json={
            "message": "Policy_Renewal_Batch failing",
            "session_id": "e2e-test-session",
            "user_id": "test_user",
            "user_role": "technical",
        },
        timeout=120,
    )
    assert r.status_code == 200
    data = r.json()
    response_text = data.get("response", "")
    assert response_text, "Empty response"
    # Must not be connection refused (mock API down)
    assert "10061" not in response_text, "Connection refused (mock API down?)"
    # If response mentions 404, agent should offer alternatives (graceful handling)
    if "404" in response_text:
        ok = any(
            phrase in response_text.lower()
            for phrase in ["try something else", "escalate", "something else", "else?", "like me to"]
        )
        if not ok:
            print(f"\n[Agent response snippet]\n{response_text[:800]}\n")
        assert ok, "Agent got 404 but did not offer fallback."


def run_all():
    print("1. Agent health...")
    test_agent_health()
    print("   OK")

    print("2. Mock API workflow status...")
    test_mock_api_workflow_status()
    print("   OK")

    print("3. Mock API incidents...")
    test_mock_api_incidents()
    print("   OK")

    print("4. Chat (Policy_Renewal_Batch failing)...")
    test_chat_quick_action()
    print("   OK")

    print("\nAll E2E API checks passed.")


if __name__ == "__main__":
    run_all()
