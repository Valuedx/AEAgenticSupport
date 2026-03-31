from unittest.mock import patch

from tools.log_tools import extract_error_blocks, get_execution_logs


def test_extract_error_blocks_scans_from_first_line_and_ignores_stacktrace_false_positives():
    log_lines = [
        "2026-03-31T10:00:00.000+05:30 [22625][WF_Check_Sharepath_Access] ERROR - Required Sharepath is Not Accessible",
        'java.lang.NullPointerException: Cannot invoke "String.equals(Object)" because AbortMeta.getAbortOptions() is null',
        "at com.automationedge.ps.workflow.steps.abort.Abort.execute(Abort.java:52)",
        "2026-03-31T10:00:01.000+05:30 [22625][WF_Check_Sharepath_Access] ERROR - Abort step failed with java.lang.NullPointerException",
        "2026-03-31T10:00:02.000+05:30 [22625][Claim_Process_Chatbot] ERROR - Workflow detected one or more steps with errors",
    ]

    blocks = extract_error_blocks(log_lines, context_lines=3)

    assert len(blocks) == 3
    assert blocks[0]["error_message"] == "Required Sharepath is Not Accessible"
    assert "NullPointerException" in blocks[1]["error_message"]
    assert blocks[0]["line_number"] == 1
    assert blocks[1]["line_number"] == 4
    assert blocks[2]["line_number"] == 5


def test_get_execution_logs_returns_full_chain_report_and_uses_full_scan_by_default():
    captured = {}

    class StubClient:
        def get_execution_logs(self, execution_id, tail=0):
            captured["execution_id"] = execution_id
            captured["tail"] = tail
            return {
                "workflow_name": "Claim Process Chatbot",
                "logs": [
                    "2026-03-31T10:00:00.000+05:30 [22625][WF_Check_Sharepath_Access] ERROR - Required Sharepath is Not Accessible",
                    'java.lang.NullPointerException: Cannot invoke "String.equals(Object)" because AbortMeta.getAbortOptions() is null',
                    "at com.automationedge.ps.workflow.steps.abort.Abort.execute(Abort.java:52)",
                    "2026-03-31T10:00:01.000+05:30 [22625][WF_Check_Sharepath_Access] ERROR - Abort step failed with java.lang.NullPointerException",
                    "2026-03-31T10:00:02.000+05:30 [22625][Claim_Process_Chatbot] ERROR - Workflow detected one or more steps with errors",
                    "2026-03-31T10:00:03.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 1",
                    "2026-03-31T10:00:04.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 2",
                    "2026-03-31T10:00:05.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 3",
                    "2026-03-31T10:00:06.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 4",
                    "2026-03-31T10:00:07.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 5",
                    "2026-03-31T10:00:08.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 6",
                    "2026-03-31T10:00:09.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 7",
                    "2026-03-31T10:00:10.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 8",
                    "2026-03-31T10:00:11.000+05:30 [22625][Claim_Process_Chatbot] INFO - downstream context line 9",
                ],
                "source_info": "unit-test",
            }

    with patch("tools.log_tools.get_ae_client", return_value=StubClient()):
        result = get_execution_logs("22625")

    assert captured["execution_id"] == "22625"
    assert captured["tail"] == 0
    assert result["success"] is True
    assert result["scan_mode"] == "full_log"
    assert result["error_block_count"] == 3
    assert result["distinct_error_count"] == 3
    assert result["primary_error"]["error_message"] == "Required Sharepath is Not Accessible"
    assert "Lines scanned from start: 14" in result["report"]
    assert "Distinct Error Patterns" in result["report"]
    assert "Chronological Error Blocks" in result["report"]
    assert "Required Sharepath is Not Accessible" in result["report"]
    assert "Abort step failed with java.lang.NullPointerException" in result["report"]
    assert "Extracted log lines (trigger + up to 50 following lines):" in result["report"]
    assert "downstream context line 9" in result["report"]
    assert "upstream cause" in result["report"].lower()
