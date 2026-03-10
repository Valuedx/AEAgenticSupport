
import os
import sys
import json
import logging

# Ensure local imports work
sys.path.insert(0, os.getcwd())

from tools.bootstrap import initialize_tooling
from tools.registry import tool_registry

def test_tool(tool_name, **kwargs):
    print(f"\nTesting tool: {tool_name}")
    try:
        result = tool_registry.execute(tool_name, **kwargs)
        if result.success:
            print(f"PASS: {tool_name}")
            # print(f"Data: {json.dumps(result.data, indent=2)[:500]}...")
            return result.data
        else:
            print(f"FAIL: {tool_name}")
            print(f"Error: {result.error}")
            return None
    except Exception as e:
        print(f"ERROR: {tool_name} raised exception")
        print(str(e))
        return None

def comprehensive_validation():
    print("Initializing tooling for comprehensive validation...")
    initialize_tooling()
    
    # 1. Request category - list recent
    print("\n--- [CATEGORY: REQUEST] ---")
    requests = test_tool("ae.request.list_recent", limit=1)
    
    request_id = None
    if requests and isinstance(requests, dict) and "result" in requests:
        items = requests["result"]
        if items and len(items) > 0:
            request_id = items[0].get("id")
            print(f"Found request_id for further testing: {request_id}")

    # 2. Workflow category - search
    print("\n--- [CATEGORY: WORKFLOW] ---")
    workflows = test_tool("ae.workflow.search", query="Bot", limit=1)

    # 3. Agent category - get status
    print("\n--- [CATEGORY: AGENT] ---")
    running_agents = test_tool("ae.agent.list_running", limit=1)
    if running_agents and isinstance(running_agents, dict) and "result" in running_agents:
        items = running_agents["result"]
        if items and len(items) > 0:
            agent_id = items[0].get("agentId")
            print(f"Found agent_id for status check: {agent_id}")
            test_tool("ae.agent.get_status", agent_id=agent_id)
        else:
            print("No running agents found.")
    else:
        # Fallback to general list if no running
        test_tool("ae.agent.list_unknown", limit=1)

    # 4. Schedule category - list/details
    print("\n--- [CATEGORY: SCHEDULE] ---")
    # Finding a workflow to check schedules for
    if workflows and isinstance(workflows, dict) and "result" in workflows:
        items = workflows["result"]
        if items and len(items) > 0:
            wf_id = items[0].get("id")
            test_tool("ae.schedule.list_for_workflow", workflow_id=wf_id)
    else:
        test_tool("ae.schedule.get_details", schedule_id="DUMMY_SCHED")

    # 5. Task category - search pending
    print("\n--- [CATEGORY: TASK] ---")
    # Using a safer limit
    test_tool("ae.task.search_pending", limit=5)

    # 6. Support category - diagnosis
    print("\n--- [CATEGORY: SUPPORT] ---")
    # Finding a failed request for real diagnosis test
    failed_reqs = test_tool("ae.request.list_failed_recently", limit=1)
    if failed_reqs and isinstance(failed_reqs, dict) and "result" in failed_reqs:
        items = failed_reqs["result"]
        if items and len(items) > 0:
            failed_id = items[0].get("id")
            print(f"Found failed request_id for diagnosis: {failed_id}")
            test_tool("ae.support.diagnose_failed_request", request_id=failed_id)
        else:
            print("No recent failed requests found to test diagnosis tool.")
    else:
        test_tool("ae.support.diagnose_agent_unavailable", agent_id="AGENT_HEALTH_CHECK")

if __name__ == "__main__":
    comprehensive_validation()
