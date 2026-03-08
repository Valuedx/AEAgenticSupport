from __future__ import annotations

from state import app_config


def test_runtime_config_reads_saved_integration_overrides(tmp_path, monkeypatch):
    store = app_config.AppConfigStore(path=str(tmp_path / "app_control_center.json"))
    saved = store.update_section(
        "integrations",
        {
            "aeTimeoutSeconds": "45",
            "aeDefaultUserId": "ops_admin",
            "cognibotBaseUrl": "http://chat.example:3978",
        },
    )

    assert saved["aeTimeoutSeconds"] == 45
    assert saved["aeDefaultUserId"] == "ops_admin"
    assert saved["cognibotBaseUrl"] == "http://chat.example:3978"

    monkeypatch.setattr(app_config, "_app_config_store", store)

    assert app_config.get_runtime_value("AE_TIMEOUT_SECONDS") == 45
    assert app_config.get_runtime_value("AE_DEFAULT_USERID") == "ops_admin"
    assert app_config.get_runtime_value("COGNIBOT_BASE_URL") == "http://chat.example:3978"


def test_public_chat_config_uses_workspace_copy_and_channel_flag(tmp_path, monkeypatch):
    store = app_config.AppConfigStore(path=str(tmp_path / "app_control_center.json"))
    store.update_section(
        "workspace",
        {
            "assistantName": "Business Support Desk",
            "technicalRoleLabel": "Platform team",
            "businessRoleLabel": "Operations lead",
            "inputPlaceholder": "Describe the business impact...",
            "technicalWelcomeMessage": "Tell me the workflow or request you need reviewed.",
            "businessWelcomeMessage": "Tell me what business outcome is blocked.",
            "quickActions": "Check payment workflow health\nSummarize open incidents",
        },
    )

    monkeypatch.setattr(app_config, "_app_config_store", store)
    monkeypatch.setitem(app_config.CONFIG, "COGNIBOT_DIRECTLINE_SECRET", "test-secret")

    chat_config = app_config.get_public_chat_config()

    assert chat_config["assistantName"] == "Business Support Desk"
    assert chat_config["technicalRoleLabel"] == "Platform team"
    assert chat_config["businessRoleLabel"] == "Operations lead"
    assert chat_config["inputPlaceholder"] == "Describe the business impact..."
    assert chat_config["technicalWelcomeMessage"] == "Tell me the workflow or request you need reviewed."
    assert chat_config["businessWelcomeMessage"] == "Tell me what business outcome is blocked."
    assert chat_config["quickActions"] == [
        "Check payment workflow health",
        "Summarize open incidents",
    ]
    assert chat_config["aistudioConfigured"] is True


def test_public_docs_config_uses_workspace_copy(tmp_path, monkeypatch):
    store = app_config.AppConfigStore(path=str(tmp_path / "app_control_center.json"))
    store.update_section(
        "workspace",
        {
            "documentationTitle": "Support Knowledge Hub",
            "documentationSubtitle": "Reference material for both business and technical support teams.",
        },
    )

    monkeypatch.setattr(app_config, "_app_config_store", store)

    docs_config = app_config.get_public_docs_config()

    assert docs_config["documentationTitle"] == "Support Knowledge Hub"
    assert (
        docs_config["documentationSubtitle"]
        == "Reference material for both business and technical support teams."
    )
