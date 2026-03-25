from __future__ import annotations

import agent_server


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
