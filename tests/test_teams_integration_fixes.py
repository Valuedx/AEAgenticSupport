from __future__ import annotations

import json
import importlib
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

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

import custom.custom_hooks as custom_hooks
import custom.functions.python.support_agent as support_agent
from custom.helpers.teams import make_approval_card
from custom.helpers.teams_proactive import extract_conversation_ref


class FakeChain:
    def __init__(self, obj):
        self.obj = obj

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.obj


class FakeCase:
    def __init__(self, **kwargs):
        defaults = {
            "case_id": "case-1",
            "state": "PLANNING",
            "owner_type": "BOT_L1",
            "owner_team": None,
            "ticket_id": "T-1",
            "thread_id": "thread-1",
            "latest_plan_json": {},
            "plan_version": 0,
            "error_signatures": [],
            "workflows_involved": [],
            "resolved_at": None,
            "resolution_summary": None,
            "updated_at": None,
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)
        self.save_calls = 0

    def save(self):
        self.save_calls += 1


class FakeApproval:
    def __init__(self, **kwargs):
        defaults = {
            "status": "PENDING",
            "requested_to": [],
            "decided_by": None,
            "decided_at": None,
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)
        self.save_calls = 0

    def save(self):
        self.save_calls += 1


class FakeConversationStateRecord:
    def __init__(self):
        self.last_user_message_id = None
        self.updated_at = None
        self.save_calls = 0

    def save(self):
        self.save_calls += 1


def _load_cognibot_hooks(monkeypatch):
    hooks_mod = ModuleType("aistudiobot.hooks")

    class ChatbotHooks:
        pass

    hooks_mod.ChatbotHooks = ChatbotHooks

    aistudiobot_mod = ModuleType("aistudiobot")
    aistudiobot_mod.hooks = hooks_mod

    dialogs_mod = ModuleType("botbuilder.dialogs")

    class ComponentDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def add_dialog(self, *_args, **_kwargs):
            pass

    class WaterfallDialog:
        def __init__(self, *_args, **_kwargs):
            pass

    class WaterfallStepContext:
        pass

    dialogs_mod.ComponentDialog = ComponentDialog
    dialogs_mod.WaterfallDialog = WaterfallDialog
    dialogs_mod.WaterfallStepContext = WaterfallStepContext

    botbuilder_mod = ModuleType("botbuilder")
    botbuilder_mod.dialogs = dialogs_mod

    monkeypatch.setitem(sys.modules, "aistudiobot", aistudiobot_mod)
    monkeypatch.setitem(sys.modules, "aistudiobot.hooks", hooks_mod)
    monkeypatch.setitem(sys.modules, "botbuilder", botbuilder_mod)
    monkeypatch.setitem(sys.modules, "botbuilder.dialogs", dialogs_mod)

    sys.modules.pop("custom_cognibot.custom_hooks", None)
    return importlib.import_module("custom_cognibot.custom_hooks")


def test_activity_to_dict_preserves_proactive_fields():
    activity = SimpleNamespace(
        text="hello",
        id="msg-1",
        conversation=SimpleNamespace(id="thread-1"),
        from_property=SimpleNamespace(id="user-1"),
        value={"action": "approve"},
        service_url="https://smba.trafficmanager.net/amer/",
        channel_id="msteams",
        channel_data={"tenant": {"id": "tenant-1"}},
        recipient=SimpleNamespace(id="bot-1"),
    )

    result = custom_hooks._activity_to_dict(activity)
    ref = extract_conversation_ref(result)

    assert result["serviceUrl"] == "https://smba.trafficmanager.net/amer/"
    assert result["channelId"] == "msteams"
    assert result["channelData"] == {"tenant": {"id": "tenant-1"}}
    assert result["recipient"] == {"id": "bot-1"}
    assert ref is not None
    assert ref["service_url"] == "https://smba.trafficmanager.net/amer"


def test_process_message_sync_approve_does_not_premark_approval(monkeypatch):
    active_case = FakeCase(case_id="case-1", state="WAITING_APPROVAL")
    approval = FakeApproval(requested_to=["user-1"])
    cog_state = SimpleNamespace(active_case_id="case-1")

    monkeypatch.setattr(custom_hooks, "save_conversation_ref", lambda activity: None)
    monkeypatch.setattr(custom_hooks, "pg_advisory_lock", lambda thread_id: nullcontext())
    monkeypatch.setattr(custom_hooks, "is_duplicate_message", lambda *_args: False)
    monkeypatch.setattr(custom_hooks, "mark_message_processed", lambda *_args: None)
    monkeypatch.setattr(
        custom_hooks,
        "CogniState",
        SimpleNamespace(objects=SimpleNamespace(get_or_create=lambda **_kwargs: (cog_state, False))),
    )
    monkeypatch.setattr(
        custom_hooks,
        "Case",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: FakeChain(active_case))),
    )
    monkeypatch.setattr(
        custom_hooks,
        "Approval",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: FakeChain(approval))),
    )
    monkeypatch.setattr(
        custom_hooks,
        "handle_support_turn",
        lambda **_kwargs: {"type": "message", "text": "executing"},
    )

    result = custom_hooks._process_message_sync(
        {
            "text": "approve",
            "id": "msg-1",
            "conversation": {"id": "thread-1"},
            "from": {"id": "user-1"},
        }
    )

    assert result == {"type": "message", "text": "executing"}
    assert approval.status == "PENDING"
    assert approval.save_calls == 0


def test_process_message_sync_blocks_approval_without_authorized_reviewers(monkeypatch):
    active_case = FakeCase(case_id="case-1", state="WAITING_APPROVAL")
    approval = FakeApproval(requested_to=[])
    cog_state = SimpleNamespace(active_case_id="case-1")

    monkeypatch.setattr(custom_hooks, "save_conversation_ref", lambda activity: None)
    monkeypatch.setattr(custom_hooks, "pg_advisory_lock", lambda thread_id: nullcontext())
    monkeypatch.setattr(custom_hooks, "is_duplicate_message", lambda *_args: False)
    monkeypatch.setattr(custom_hooks, "mark_message_processed", lambda *_args: None)
    monkeypatch.setattr(
        custom_hooks,
        "CogniState",
        SimpleNamespace(objects=SimpleNamespace(get_or_create=lambda **_kwargs: (cog_state, False))),
    )
    monkeypatch.setattr(
        custom_hooks,
        "Case",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: FakeChain(active_case))),
    )
    monkeypatch.setattr(
        custom_hooks,
        "Approval",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: FakeChain(approval))),
    )

    result = custom_hooks._process_message_sync(
        {
            "text": "approve",
            "id": "msg-2",
            "conversation": {"id": "thread-1"},
            "from": {"id": "user-1"},
        }
    )

    assert result["text"] == (
        "This approval has no authorized reviewers configured. "
        "Please contact an administrator."
    )


def test_prepend_result_prefix_inserts_text_into_adaptive_card_body():
    result = make_approval_card("case-1", "Restart service", reviewers=["tech-1"])

    updated = custom_hooks._prepend_result_prefix(
        result, "This is a recurrence context.\n\n"
    )

    card_body = updated["attachments"][0]["content"]["body"]
    assert card_body[0] == {
        "type": "TextBlock",
        "text": "This is a recurrence context.",
        "wrap": True,
    }
    assert updated["text"].startswith("Approval Required")


def test_execute_plan_returns_no_reviewer_message_when_roster_empty(monkeypatch):
    case = FakeCase(case_id="case-1", state="EXECUTING")

    monkeypatch.setattr(
        support_agent, "classify_step", lambda step: ("high_risk", True)
    )
    monkeypatch.setattr(support_agent, "pick_onshift_techs", lambda **_kwargs: [])
    monkeypatch.setattr(
        support_agent,
        "Approval",
        SimpleNamespace(
            objects=SimpleNamespace(
                create=lambda **_kwargs: pytest.fail("Approval should not be created")
            )
        ),
    )

    result = support_agent._execute_plan(
        SimpleNamespace(call=lambda *_args, **_kwargs: {}),
        case,
        {"steps": [{"index": 1, "tool_ref": "/tools/risky", "inputs": {}}]},
    )

    assert result["text"].startswith("No support technicians are currently on shift.")
    assert case.state == "PLANNING"


def test_execute_plan_runs_approved_risky_plan_without_reasking(monkeypatch):
    case = FakeCase(case_id="case-1", state="EXECUTING", plan_version=1)
    client_calls = []

    def _client_call(tool_ref, inputs, idempotency_key=None):
        client_calls.append((tool_ref, inputs, idempotency_key))
        return {}

    monkeypatch.setattr(
        support_agent, "classify_step", lambda step: ("high_risk", True)
    )
    monkeypatch.setattr(
        support_agent,
        "Approval",
        SimpleNamespace(
            objects=SimpleNamespace(
                create=lambda **_kwargs: pytest.fail("Approval should not be recreated")
            )
        ),
    )

    result = support_agent._execute_plan(
        SimpleNamespace(call=_client_call),
        case,
        {
            "steps": [{"index": 1, "tool_ref": "/tools/risky", "inputs": {"x": 1}}],
            "issue_bucket": "GENERIC",
        },
        approval_granted=True,
    )

    assert result == "Done. Please confirm if you received the output file."
    assert client_calls == [("/tools/risky", {"x": 1}, "case-1:1:1")]
    assert case.state == "RESOLVED_PENDING_CONFIRMATION"


def test_agentic_handle_support_turn_marks_pending_approval_after_gateway_accepts(monkeypatch):
    case = FakeCase(case_id="case-1", state="WAITING_APPROVAL")
    approval = FakeApproval(requested_to=["reviewer-1"])
    session = SimpleNamespace(pending_action={"tool": "dangerous"}, pending_action_summary="")

    class FakeGateway:
        def get_or_create_session(self, *_args):
            return session

        def process_message(self, **_kwargs):
            return "approved"

    monkeypatch.setattr(support_agent, "_USE_AGENTIC", True)
    monkeypatch.setattr(support_agent, "_get_gateway", lambda: FakeGateway())
    monkeypatch.setattr(support_agent, "_get_or_create_case", lambda thread_id: case)
    monkeypatch.setattr(
        support_agent,
        "_sync_state_from_gateway",
        lambda _gw, _thread_id, synced_case: setattr(synced_case, "state", "EXECUTING"),
    )
    monkeypatch.setattr(
        support_agent,
        "CogniState",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: FakeChain(None))),
    )
    monkeypatch.setattr(
        support_agent,
        "Approval",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: FakeChain(approval))),
    )

    result = support_agent.handle_support_turn(
        thread_id="thread-1",
        teams_message_id="msg-1",
        user_text="approve",
        raw_activity={"from": {"id": "reviewer-1"}, "user_type": "technical"},
    )

    assert result == {"type": "message", "text": "approved"}
    assert session.pending_action["authorized_users"] == ["reviewer-1"]
    assert approval.status == "APPROVED"
    assert approval.decided_by == "reviewer-1"
    assert approval.save_calls == 1


@pytest.mark.asyncio
async def test_custom_api_messages_hook_thin_proxy_forwards_teams_identity(monkeypatch):
    posted = {}
    saved_refs = []
    marked = []
    conv_state = FakeConversationStateRecord()

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

    monkeypatch.setenv("AI_STUDIO_THIN_PROXY_MODE", "true")
    monkeypatch.setenv("AGENT_SERVER_URL", "http://agent.test")
    monkeypatch.setenv("AGENT_TIMEOUT", "45")
    monkeypatch.setattr(custom_hooks, "save_conversation_ref", lambda activity: saved_refs.append(activity))
    monkeypatch.setattr(custom_hooks, "pg_advisory_lock", lambda _thread_id: nullcontext())
    monkeypatch.setattr(custom_hooks, "is_duplicate_message", lambda *_args: False)
    monkeypatch.setattr(
        custom_hooks,
        "mark_message_processed",
        lambda thread_id, request_id: marked.append((thread_id, request_id)),
    )
    monkeypatch.setattr(
        custom_hooks,
        "CogniState",
        SimpleNamespace(
            objects=SimpleNamespace(
                get_or_create=lambda **_kwargs: (conv_state, False)
            )
        ),
    )
    monkeypatch.setattr(custom_hooks.requests, "post", _fake_post)

    activity = {
        "text": "Please investigate the failed workflow",
        "id": "msg-1",
        "conversation": {"id": "thread-1"},
        "from": {
            "id": "user-1",
            "name": "Pat User",
            "aadObjectId": "aad-123",
        },
        "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "recipient": {"id": "bot-1"},
        "channelData": {
            "tenant": {"id": "tenant-1"},
            "team": {"id": "team-1"},
            "channel": {"id": "channel-1"},
        },
        "user_type": "business",
    }

    request = SimpleNamespace(
        headers={"Authorization": "Bearer teams-auth"},
        body=json.dumps(
            {
                "additionalInfo": {
                    "conversation_details": {
                        "conversation_id": "thread-1",
                        "chat_channel": "msteams",
                    },
                    "uuid": "uuid-1",
                },
                "chatBotID": "chatbot-1",
            }
        ).encode("utf-8"),
    )

    result = await custom_hooks.CustomChatbotHooks.api_messages_hook(request, activity)

    assert result == {
        "type": "message",
        "text": "I'm working on that now. I'll send the result here shortly.",
    }
    assert saved_refs == [activity]
    assert marked == [("thread-1", "msg-1")]
    assert conv_state.last_user_message_id == "msg-1"
    assert conv_state.save_calls == 1
    assert posted["url"] == "http://agent.test/chat/async"
    assert posted["timeout"] == 15
    assert posted["json"]["request_id"] == "msg-1"
    assert posted["json"]["session_id"] == "thread-1"
    assert posted["json"]["user_id"] == "user-1"
    assert posted["json"]["user_name"] == "Pat User"
    assert posted["json"]["user_role"] == "business"
    assert posted["json"]["user_metadata"]["aad_object_id"] == "aad-123"
    assert posted["json"]["user_metadata"]["tenant_id"] == "tenant-1"
    assert posted["json"]["user_metadata"]["team_id"] == "team-1"
    assert posted["json"]["user_metadata"]["teams_channel_id"] == "channel-1"
    assert posted["json"]["reply_channel"]["channel"] == "msteams"
    assert posted["json"]["reply_channel"]["service_url"] == "https://smba.trafficmanager.net/amer/"
    assert posted["json"]["reply_channel"]["auth_header"] == "Bearer teams-auth"
    assert posted["json"]["reply_channel"]["aistudio_additional_info"] == {
        "conversation_details": {
            "conversation_id": "thread-1",
            "chat_channel": "msteams",
        },
        "uuid": "uuid-1",
    }
    assert posted["json"]["reply_channel"]["aistudio_chatbot_id"] == "chatbot-1"


@pytest.mark.asyncio
async def test_custom_api_messages_hook_thin_proxy_duplicate_short_circuits(monkeypatch):
    monkeypatch.setenv("AI_STUDIO_THIN_PROXY_MODE", "true")
    monkeypatch.setattr(custom_hooks, "save_conversation_ref", lambda _activity: None)
    monkeypatch.setattr(custom_hooks, "pg_advisory_lock", lambda _thread_id: nullcontext())
    monkeypatch.setattr(custom_hooks, "is_duplicate_message", lambda *_args: True)
    monkeypatch.setattr(
        custom_hooks,
        "mark_message_processed",
        lambda *_args: pytest.fail("duplicate should not be re-marked"),
    )
    monkeypatch.setattr(
        custom_hooks.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("duplicate should not dispatch"),
    )

    result = await custom_hooks.CustomChatbotHooks.api_messages_hook(
        None,
        {
            "text": "Please investigate the failed workflow",
            "id": "msg-dup",
            "conversation": {"id": "thread-1"},
            "from": {"id": "user-1"},
            "channelId": "msteams",
        },
    )

    assert result == {
        "type": "message",
        "text": "I'm already working on that message. I'll send the result here shortly.",
    }


@pytest.mark.asyncio
async def test_custom_api_messages_hook_thin_proxy_dispatch_failure_returns_queue_error(monkeypatch):
    conv_state = FakeConversationStateRecord()

    monkeypatch.setenv("AI_STUDIO_THIN_PROXY_MODE", "true")
    monkeypatch.setattr(custom_hooks, "save_conversation_ref", lambda _activity: None)
    monkeypatch.setattr(custom_hooks, "pg_advisory_lock", lambda _thread_id: nullcontext())
    monkeypatch.setattr(custom_hooks, "is_duplicate_message", lambda *_args: False)
    monkeypatch.setattr(custom_hooks, "mark_message_processed", lambda *_args: None)
    monkeypatch.setattr(
        custom_hooks,
        "CogniState",
        SimpleNamespace(
            objects=SimpleNamespace(
                get_or_create=lambda **_kwargs: (conv_state, False)
            )
        ),
    )
    monkeypatch.setattr(
        custom_hooks,
        "handle_support_turn",
        lambda **_kwargs: pytest.fail("thin proxy must not fall back inline"),
    )

    def _broken_post(*_args, **_kwargs):
        raise RuntimeError("agent down")

    monkeypatch.setattr(custom_hooks.requests, "post", _broken_post)

    result = await custom_hooks.CustomChatbotHooks.api_messages_hook(
        None,
        {
            "text": "Check this issue",
            "id": "msg-err",
            "conversation": {"id": "thread-1"},
            "from": {"id": "user-1"},
            "channelId": "msteams",
        },
    )

    assert result == {
        "type": "message",
        "text": "I couldn't queue your request right now. Please try again in a moment.",
    }


@pytest.mark.asyncio
async def test_custom_api_reply_hook_handles_thin_proxy_payload(monkeypatch):
    sent = []

    monkeypatch.setattr(
        custom_hooks,
        "_send_thin_proxy_reply_sync",
        lambda payload: sent.append(payload),
    )

    body = {
        "thin_proxy_reply": {
            "activity": {"type": "message", "text": "Final reply"},
            "reply_channel": {"channel": "msteams", "conversation_id": "thread-1"},
        },
        "additionalInfo": {
            "auth_header": "Bearer teams-auth",
            "conversation_details": {
                "conversation_id": "thread-1",
                "chat_channel": "msteams",
            },
            "uuid": "__thin_proxy_async__",
        },
    }

    await custom_hooks.CustomChatbotHooks.api_reply_hook(None, body)

    assert sent == [
        {
            "activity": {"type": "message", "text": "Final reply"},
            "reply_channel": {
                "channel": "msteams",
                "conversation_id": "thread-1",
            },
        }
    ]
    assert body == {
        "additionalInfo": {
            "auth_header": "Bearer teams-auth",
            "conversation_details": {
                "conversation_id": "thread-1",
                "chat_channel": "msteams",
            },
            "uuid": "__thin_proxy_handled__",
        }
    }


@pytest.mark.asyncio
async def test_cognibot_api_messages_hook_streams_progress_to_teams(monkeypatch):
    cognibot_hooks = _load_cognibot_hooks(monkeypatch)
    proactive_calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            return [
                b"event: progress",
                b'data: "Investigating..."',
                b"event: done",
                b'data: "Final reply"',
            ]

    class ImmediateThread:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(cognibot_hooks, "_save_conversation_ref", lambda activity: None)
    monkeypatch.setattr(
        cognibot_hooks, "_send_proactive_sync", lambda thread_id, text: proactive_calls.append((thread_id, text)) or True
    )
    monkeypatch.setattr(
        cognibot_hooks.requests, "post", lambda *_args, **_kwargs: FakeResponse()
    )
    monkeypatch.setattr(cognibot_hooks.threading, "Thread", ImmediateThread)

    activity = SimpleNamespace(
        text="Check status",
        conversation=SimpleNamespace(id="conv-1"),
        from_property=SimpleNamespace(id="user-1"),
        user_type="technical",
    )

    result = await cognibot_hooks.CustomChatbotHooks.api_messages_hook(None, activity)

    assert result == {"type": "message", "text": "Final reply"}
    assert proactive_calls == [("conv-1", "Investigating...")]
