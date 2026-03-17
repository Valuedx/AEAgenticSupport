
import sys
import os
import asyncio
import json
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_server.tools.agent_tools import agent_analyze_logs

async def run_detailed_test():
    print("--- Detailed Agent Logs Flow Test ---")
    
    # Mock AE Client
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {"agentId": "agent-1", "agentName": "Agent One", "agentState": "CONNECTED"},
        {"agentId": "agent-2", "agentName": "Agent Two", "agentState": "STOPPED"},
        {"agentId": "agent-3", "agentName": "Agent Three", "agentState": "ACTIVE"}
    ]
    # Mock polling to finish immediately
    mock_client.request_agent_debug_logs.return_value = {"id": "req-123"}
    mock_client.get_agent_debug_logs.return_value = {"status": "COMPLETE", "logFileLink": "http://dummy/logs.zip"}
    mock_client.get.return_value = b"dummy zip content" # Will fail zip processing but that's okay for flow test

    with patch("mcp_server.tools.agent_tools.get_ae_client", return_value=mock_client):
        # 1. Test listing agents when ID is missing
        print("\nTest 1: List Agents (empty ID)")
        res1 = json.loads(await agent_analyze_logs(agent_id=""))
        running = res1.get('running_agents', [])
        print(f"Running agents: {[a['agent_name'] for a in running]}")
        assert len(running) == 2 # Agent One (CONNECTED) and Agent Three (ACTIVE)
        print("SUCCESS: Test 1 Passed")

        # 2. Test Case-Insensitive Name Resolution
        print("\nTest 2: Name Resolution (case-insensitive 'agent one')")
        # We mock time.sleep to bypass the 5s delay
        with patch("time.sleep", return_value=None):
            res2 = await agent_analyze_logs(agent_id="agent one")
        
        if "must be ACTIVE/RUNNING" in res2:
             print(f"FAILURE: Test 2 Failed: Unexpected error: {res2}")
        elif "not found" in res2:
             print("FAILURE: Test 2 Failed: Agent not found")
        else:
             print("SUCCESS: Test 2 Passed: Resolved 'agent one' and bypassed strict RUNNING check")

        # 3. Test Ambiguity or Incorrect Names
        print("\nTest 3: Incorrect name")
        res3 = json.loads(await agent_analyze_logs(agent_id="NonExistent"))
        print(f"Error: {res3.get('error')}")
        assert "not found" in res3.get('error', '').lower()
        print("SUCCESS: Test 3 Passed")

        # 4. Test State Blocking
        print("\nTest 4: State Blocking (STOPPED agent)")
        res4 = json.loads(await agent_analyze_logs(agent_id="agent-2"))
        print(f"Error: {res4.get('error')}")
        assert "must be ACTIVE/RUNNING" in res4.get('error', '')
        print("SUCCESS: Test 4 Passed")

        # 5. Test Auto-Selection (Single agent discovery)
        print("\nTest 5: Auto-Selection (Discovery with exactly 1 agent)")
        mock_client.list_agents.return_value = [
            {"agentId": "only-one", "agentName": "Solo Agent", "agentState": "RUNNING"},
            {"agentId": "off", "agentName": "Off Agent", "agentState": "STOPPED"}
        ]
        # Bypass extraction phase
        with patch("time.sleep", return_value=None):
            res5 = await agent_analyze_logs(agent_id="")
        
        # If it return a JSON string that is NOT the listing message, it means it proceeded.
        # Proceeding normally results in extraction errors or success (in this mock, error likely)
        if "Please provide an agent_id" not in res5:
             print("SUCCESS: Test 5 Passed - Automatically selected candidate")
        else:
             print(f"FAILURE: Test 5 Failed - Returned selection list instead of auto-selecting: {res5[:100]}...")

if __name__ == "__main__":
    asyncio.run(run_detailed_test())
