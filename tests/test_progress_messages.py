from gateway.progress import ProgressCallback


def test_business_progress_messages_use_friendly_copy():
    messages: list[str] = []
    progress = ProgressCallback(
        send_fn=messages.append,
        user_role="business",
        min_interval=0,
    )

    progress.on_phase("investigating")
    progress.on_tool_start("check_workflow_status", {})
    progress.on_iteration(4, 12)

    assert messages[0] == "I'm looking into this now..."
    assert messages[1] == "Checking the latest process status..."
    assert messages[2] == "I'm still investigating this..."


def test_technical_progress_failure_message_is_clear():
    messages: list[str] = []
    progress = ProgressCallback(
        send_fn=messages.append,
        user_role="technical",
        min_interval=0,
    )

    progress.on_tool_done(
        "call_ae_api",
        success=False,
        result_hint="404 not found for agent logs endpoint",
    )

    assert messages == [
        "I found an issue while running call_ae_api: 404 not found for agent logs endpoint"
    ]
