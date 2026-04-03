from __future__ import annotations

from unittest.mock import patch

import agent_server


def test_webchat_login_required_for_authenticated_chat():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            response = client.post(
                "/api/webchat/chat",
                json={"message": "check claims", "user_role": "technical"},
            )

            assert response.status_code == 401
            assert response.get_json()["error"] == "unauthorized"


def test_webchat_page_contains_login_gate():
    agent_server.app.config["TESTING"] = True
    with agent_server.app.test_client() as client:
        response = client.get("/")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Web Chat Login" in body
        assert "loginUsername" in body
        assert "loginPassword" in body


def test_webchat_login_sets_exact_username_session():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            with patch("agent_server._webchat_username_exists_in_ae", return_value=True):
                response = client.post(
                    "/api/webchat/auth/login",
                    json={"username": "Claims.User", "password": "Edge@1234"},
                )

            assert response.status_code == 200
            payload = response.get_json()
            assert payload["success"] is True
            assert payload["username"] == "Claims.User"
            assert payload["user_id"] == "webchat:Claims.User"

            me = client.get("/api/webchat/auth/me")
            me_payload = me.get_json()
            assert me_payload["authenticated"] is True
            assert me_payload["username"] == "Claims.User"
            assert me_payload["user_id"] == "webchat:Claims.User"


def test_webchat_auth_me_can_rotate_chat_session_on_refresh():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            with patch("agent_server._webchat_username_exists_in_ae", return_value=True):
                login = client.post(
                    "/api/webchat/auth/login",
                    json={"username": "claims.user", "password": "Edge@1234"},
                )
            assert login.status_code == 200
            first_session_id = login.get_json()["chat_session_id"]

            refreshed = client.get("/api/webchat/auth/me?refresh_chat_session=1")

            assert refreshed.status_code == 200
            refreshed_payload = refreshed.get_json()
            assert refreshed_payload["authenticated"] is True
            assert refreshed_payload["username"] == "claims.user"
            assert refreshed_payload["user_id"] == "webchat:claims.user"
            assert refreshed_payload["chat_session_id"].startswith("webchat-claims_user-")
            assert refreshed_payload["chat_session_id"] != first_session_id


def test_webchat_authenticated_chat_uses_session_username_only():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            with patch("agent_server._webchat_username_exists_in_ae", return_value=True):
                login = client.post(
                    "/api/webchat/auth/login",
                    json={"username": "claims.user", "password": "Edge@1234"},
                )
            assert login.status_code == 200

            with patch("agent_server.handle_chat_message", return_value="ok") as mock_handle:
                response = client.post(
                    "/api/webchat/chat",
                    json={
                        "message": "run claims",
                        "user_role": "technical",
                        "user_id": "spoofed-user",
                        "user_name": "Spoofed.Name",
                    },
                )

            assert response.status_code == 200
            assert response.get_json()["response"] == "ok"
            kwargs = mock_handle.call_args.kwargs
            assert kwargs["user_id"] == "webchat:claims.user"
            assert kwargs["user_name"] == "claims.user"
            assert kwargs["session_id"].startswith("webchat-claims_user-")


def test_webchat_authenticated_chat_uses_refreshed_session_id():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            with patch("agent_server._webchat_username_exists_in_ae", return_value=True):
                login = client.post(
                    "/api/webchat/auth/login",
                    json={"username": "claims.user", "password": "Edge@1234"},
                )
            assert login.status_code == 200

            refreshed = client.get("/api/webchat/auth/me?refresh_chat_session=1")
            refreshed_session_id = refreshed.get_json()["chat_session_id"]

            with patch("agent_server.handle_chat_message", return_value="ok") as mock_handle:
                response = client.post(
                    "/api/webchat/chat",
                    json={
                        "message": "run claims",
                        "user_role": "technical",
                    },
                )

            assert response.status_code == 200
            assert response.get_json()["response"] == "ok"
            kwargs = mock_handle.call_args.kwargs
            assert kwargs["session_id"] == refreshed_session_id


def test_webchat_stream_chat_uses_session_identity():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            with patch("agent_server._webchat_username_exists_in_ae", return_value=True):
                login = client.post(
                    "/api/webchat/auth/login",
                    json={"username": "claims.user", "password": "Edge@1234"},
                )
            assert login.status_code == 200

            with patch("agent_server.handle_chat_message", return_value="stream-ok") as mock_handle:
                response = client.post(
                    "/api/webchat/chat/stream",
                    json={"message": "run claims", "user_role": "technical"},
                )

            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert "event: done" in body
            assert "stream-ok" in body
            kwargs = mock_handle.call_args.kwargs
            assert kwargs["user_id"] == "webchat:claims.user"
            assert kwargs["user_name"] == "claims.user"


def test_webchat_login_rejects_username_not_found_in_ae():
    agent_server.app.config["TESTING"] = True
    with patch.dict(
        agent_server.CONFIG,
        {
            "WEBCHAT_AUTH_ENABLED": True,
            "WEBCHAT_LOGIN_PASSWORD": "Edge@1234",
        },
        clear=False,
    ):
        with agent_server.app.test_client() as client:
            with patch("agent_server._webchat_username_exists_in_ae", return_value=False):
                response = client.post(
                    "/api/webchat/auth/login",
                    json={"username": "unknown.user", "password": "Edge@1234"},
                )

            assert response.status_code == 401
            payload = response.get_json()
            assert payload["success"] is False
            assert "does not match any AutomationEdge user" in payload["error"]

            me = client.get("/api/webchat/auth/me")
            me_payload = me.get_json()
            assert me_payload["authenticated"] is False
