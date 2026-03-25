from __future__ import annotations

from types import SimpleNamespace

import agent_server
from state.conversation_state import ConversationPhase


def test_build_cognibot_reply_payload_wraps_async_reply():
    payload = agent_server._build_cognibot_reply_payload(
        {
            "channel": "webchat",
            "conversation_id": "conv-1",
            "service_url": "http://localhost:3978",
            "user_id": "user-1",
            "user_name": "Pat User",
            "bot_id": "bot-1",
            "bot_name": "Agentic AI Bot",
        },
        {"type": "message", "text": "Done"},
    )

    assert payload["thin_proxy_reply"]["activity"]["text"] == "Done"
    assert payload["thin_proxy_reply"]["reply_channel"]["conversation_id"] == "conv-1"
    assert payload["additionalInfo"]["conversation_details"] == {
        "bot_id": "bot-1",
        "bot_name": "Agentic AI Bot",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "user_name": "Pat User",
        "chat_channel": "emulator",
        "service_url": "http://localhost:3978",
        "model_conversation_id": "conv-1",
    }


def test_send_reply_channel_message_posts_to_aistudio_reply(monkeypatch):
    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        agent_server,
        "_cognibot_reply_url",
        lambda: "http://cognibot.local/api/reply",
    )

    def _fake_post(url, json=None, headers=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        posted["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(agent_server.requests, "post", _fake_post)

    agent_server._send_reply_channel_message(
        {
            "channel": "msteams",
            "conversation_id": "conv-1",
            "service_url": "https://smba.trafficmanager.net/amer/",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "user_name": "Pat User",
            "bot_id": "bot-1",
            "bot_name": "Agentic AI Bot",
            "auth_header": "Bearer teams-auth",
        },
        "Final reply",
    )

    assert posted["url"] == "http://cognibot.local/api/reply"
    assert posted["headers"] == {"Content-Type": "application/json"}
    assert posted["timeout"] == 20
    assert posted["json"]["thin_proxy_reply"]["reply_channel"]["channel"] == "msteams"
    assert posted["json"]["additionalInfo"]["auth_header"] == "Bearer teams-auth"
    assert posted["json"]["additionalInfo"]["conversation_details"]["chat_channel"] == "msteams"


def test_validate_reply_channel_allows_teams_without_auth_header(monkeypatch):
    monkeypatch.setattr(
        agent_server,
        "_cognibot_reply_url",
        lambda: "http://cognibot.local/api/reply",
    )

    agent_server._validate_reply_channel(
        {
            "channel": "msteams",
            "conversation_id": "conv-1",
            "service_url": "https://smba.trafficmanager.net/amer/",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "bot_id": "bot-1",
        }
    )


def test_api_approvals_decision_accepts_enum_phase(monkeypatch):
    client = agent_server.app.test_client()

    monkeypatch.setattr(
        agent_server._main_gateway,
        "process_message",
        lambda cid, decision, user_id=None: {
            "conversation_id": cid,
            "decision": decision,
            "approver_id": user_id,
        },
    )

    class FakeConversationState:
        exists_in_store = True
        phase = ConversationPhase.AWAITING_APPROVAL

    monkeypatch.setattr(
        "state.conversation_state.ConversationState.load",
        lambda cid: FakeConversationState(),
    )

    response = client.post(
        "/api/approvals/decision",
        json={
            "conversation_id": "conv-1",
            "decision": "approve",
            "approver_id": "admin-1",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "decision": "approve",
        "agent_response": {
            "conversation_id": "conv-1",
            "decision": "approve",
            "approver_id": "admin-1",
        },
    }


def test_api_approvals_decision_404s_when_state_missing(monkeypatch):
    client = agent_server.app.test_client()

    monkeypatch.setattr(
        "state.conversation_state.ConversationState.load",
        lambda cid: SimpleNamespace(exists_in_store=False, phase=ConversationPhase.IDLE),
    )

    response = client.post(
        "/api/approvals/decision",
        json={
            "conversation_id": "conv-missing",
            "decision": "approve",
        },
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Conversation state not found"}
