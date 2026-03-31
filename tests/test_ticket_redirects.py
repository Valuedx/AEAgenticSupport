from agents.orchestrator import Orchestrator
from tools.general_tools import call_ae_api


def test_call_ae_api_blocks_support_ticket_like_endpoint_before_server_call():
    result = call_ae_api(
        method="POST",
        endpoint="/ac/a/v1/support/tokut",
        body='{"process_name":"MG312 BOT2","description":"Execution failed"}',
    )

    assert result["error"] == "ticket_tool_required"
    assert "ae.ticket.create" in result["message"]


def test_orchestrator_extracts_ticket_args_from_malformed_call_ae_api_body():
    orch = Orchestrator()

    extracted = orch._extract_ticket_args_from_call_ae_api(
        {
            "method": "POST",
            "endpoint": "/an/api/v1/support/tokut",
            "body": 'process_name: "MG312 BOT2", description: "Execution failed for MG312 BOT2 on agent se70eabc-aata-4c5d-me-4b096626e2f9"',
        }
    )

    assert extracted is not None
    assert extracted["process_name"] == "MG312 BOT2"
    assert "Execution failed for MG312 BOT2" in extracted["description"]
    assert extracted["request_type"] == "Incident"
