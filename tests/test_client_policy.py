from config.client_policy import build_client_prompt_addendum, format_client_message, load_client_policy


def test_load_client_policy_merges_default_and_org_specific_file():
    policy = load_client_policy(org_code="AEGEMS", metadata={"org_code": "AEGEMS"})

    assert "default.json" in policy["_loaded_files"]
    assert "aegems.json" in policy["_loaded_files"]
    assert policy["client_name"] == "AEGEMS Client Policy"


def test_build_client_prompt_addendum_includes_org_specific_prompt_text():
    prompt = build_client_prompt_addendum(org_code="AEGEMS", metadata={"org_code": "AEGEMS"})

    assert "AEGEMS Workflow Policy" in prompt
    assert "always confirm the related application health before retrying the workflow" in prompt


def test_format_client_message_uses_client_override():
    message = format_client_message(
        "retry_blocked_unhealthy",
        "{application} system is currently unavailable or under maintenance. Please try again later.",
        org_code="AEGEMS",
        metadata={"org_code": "AEGEMS"},
        application="TEBT",
    )

    assert message == "TEBT is currently unavailable for AEGEMS. Please try again later."
