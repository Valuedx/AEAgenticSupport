from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="test",
        INSTALLED_APPS=["django.contrib.contenttypes", "custom"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        USE_TZ=True,
    )

import django

django.setup()

import custom.functions.python.actions as actions


class FakeConvState:
    def __init__(self):
        self.conv_inputs = {}
        self.dialog_inputs = {}

    def get_dialog_input_as_param(self, dialog_name, key):
        return self.dialog_inputs.get(dialog_name, {}).get(key)

    def get_conv_input_as_param(self, key):
        return self.conv_inputs.get(key)

    def add_dialog_input_as_param(self, dialog_name, key, value):
        self.dialog_inputs.setdefault(dialog_name, {})[key] = value

    def add_conv_input_as_param(self, key, value):
        self.conv_inputs[key] = value


class FakeUserState:
    def __init__(self, **values):
        self.values = values

    def get_user_input_as_param(self, key):
        return self.values.get(key)


class FakeQuery:
    def __init__(self, items):
        self.items = list(items)

    def exclude(self, **kwargs):
        excluded = set(kwargs.get("state__in", []))
        return FakeQuery([item for item in self.items if item.state not in excluded])

    def order_by(self, *_args, **_kwargs):
        return self

    def __getitem__(self, index):
        return self.items[index]


@pytest.mark.asyncio
async def test_queue_support_turn_posts_async_payload_and_saves_state(monkeypatch):
    posted = {}
    conv_state = FakeConvState()
    user_state = FakeUserState(user_email="pat@example.com", user_team="Ops")

    class FakeResponse:
        content = b'{"queued": true}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"queued": True}

    def _fake_post(url, json=None, timeout=None, **_kwargs):
        posted["url"] = url
        posted["json"] = json
        posted["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("AGENT_SERVER_URL", "http://agent.test")
    monkeypatch.setenv("AGENT_TIMEOUT", "45")
    monkeypatch.setattr(actions.requests, "post", _fake_post)

    context = SimpleNamespace(
        activity=SimpleNamespace(
            text="Please investigate the failed workflow",
            id="msg-1",
            conversation=SimpleNamespace(id="thread-1"),
            from_property=SimpleNamespace(
                id="user-1",
                name="Pat User",
                aad_object_id="aad-123",
            ),
            recipient=SimpleNamespace(id="bot-1", name="Agentic AI Bot"),
            service_url="https://smba.trafficmanager.net/amer/",
            channel_id="msteams",
            channel_data={
                "tenant": {"id": "tenant-1"},
                "team": {"id": "team-1"},
                "channel": {"id": "channel-1"},
            },
            user_type="business",
        )
    )

    result = await actions.queue_support_turn(
        context, "SupportDialog", conv_state, user_state
    )

    assert result == {
        "success": True,
        "queued": True,
        "response": "I'm working on that now. I'll send the result here shortly.",
        "request_id": "msg-1",
        "session_id": "thread-1",
    }
    assert posted["url"] == "http://agent.test/chat/async"
    assert posted["timeout"] == 15
    assert posted["json"]["message"] == "Please investigate the failed workflow"
    assert posted["json"]["user_role"] == "business"
    assert posted["json"]["user_email"] == "pat@example.com"
    assert posted["json"]["user_team"] == "Ops"
    assert posted["json"]["user_metadata"]["aad_object_id"] == "aad-123"
    assert posted["json"]["reply_channel"]["channel"] == "msteams"
    assert posted["json"]["reply_channel"]["conversation_id"] == "thread-1"
    assert conv_state.conv_inputs["ae_support_queue_status"] == "queued"
    assert conv_state.conv_inputs["ae_support_request_id"] == "msg-1"
    assert conv_state.conv_inputs["response"] == "I'm working on that now. I'll send the result here shortly."
    assert (
        conv_state.dialog_inputs["SupportDialog"]["ae_support_session_id"] == "thread-1"
    )


@pytest.mark.asyncio
async def test_get_support_session_status_formats_active_cases(monkeypatch):
    conv_state = FakeConvState()
    cases = [
        SimpleNamespace(
            case_id="CASE-2",
            state="WAITING_APPROVAL",
            workflows_involved=["wf_beta"],
        ),
        SimpleNamespace(
            case_id="CASE-1",
            state="PLANNING",
            workflows_involved=["wf_alpha"],
        ),
        SimpleNamespace(
            case_id="CASE-0",
            state="CLOSED",
            workflows_involved=["wf_old"],
        ),
    ]

    monkeypatch.setattr(
        actions,
        "Case",
        SimpleNamespace(
            objects=SimpleNamespace(filter=lambda **_kwargs: FakeQuery(cases))
        ),
    )

    context = SimpleNamespace(
        activity=SimpleNamespace(conversation=SimpleNamespace(id="thread-1"))
    )

    result = await actions.get_support_session_status(
        context,
        "SupportDialog",
        conv_state,
        FakeUserState(),
    )

    assert result["success"] is True
    assert result["session_id"] == "thread-1"
    assert result["response"] == (
        "Current session status:\n"
        "- [CASE-2] WAITING_APPROVAL | Workflows: ['wf_beta']\n"
        "- [CASE-1] PLANNING | Workflows: ['wf_alpha']"
    )
    assert conv_state.conv_inputs["ae_support_status_response"] == result["response"]
    assert conv_state.conv_inputs["response"] == result["response"]


@pytest.mark.asyncio
async def test_get_support_session_status_prefers_gateway_state(monkeypatch):
    conv_state = FakeConvState()

    monkeypatch.setattr(
        actions,
        "_load_gateway_state_sync",
        lambda _session_id: SimpleNamespace(
            exists_in_store=True,
            phase=SimpleNamespace(value="investigating"),
            summary="Investigating the failed claims workflow",
            affected_workflows=["claims_daily"],
            pending_action_summary="Waiting for workflow diagnostics",
            is_human_handoff=False,
        ),
    )
    monkeypatch.setattr(
        actions,
        "Case",
        SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **_kwargs: pytest.fail("gateway state should win")
            )
        ),
    )

    context = SimpleNamespace(
        activity=SimpleNamespace(conversation=SimpleNamespace(id="thread-9"))
    )

    result = await actions.get_support_session_status(
        context,
        "SupportDialog",
        conv_state,
        FakeUserState(),
    )

    assert result == {
        "success": True,
        "response": (
            "Current session status:\n"
            "- Phase: INVESTIGATING\n"
            "- Summary: Investigating the failed claims workflow\n"
            "- Workflows: ['claims_daily']\n"
            "- Pending action: Waiting for workflow diagnostics"
        ),
        "session_id": "thread-9",
    }
    assert conv_state.conv_inputs["ae_support_status_response"] == result["response"]
    assert conv_state.conv_inputs["response"] == result["response"]
