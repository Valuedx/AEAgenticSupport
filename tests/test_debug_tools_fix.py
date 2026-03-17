
import sys
import os
import asyncio
import json
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.agent_debug_tools import analyze_agent_logs

async def verify_debug_tools():
    print("--- Verifying Agent Debug Tools (Fix Confirmation) ---")
    
    # Mock the client
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {"agentId": "123", "agentName": "Test Agent", "agentState": "RUNNING", "uuid": "uuid-123"}
    ]
    mock_client.request_agent_debug_logs.return_value = {"id": "req-456"}
    mock_client.get_agent_debug_logs.return_value = {"status": "COMPLETE", "logFileLink": "http://link"}
    
    with patch("tools.agent_debug_tools.get_ae_client", return_value=mock_client), \
         patch("time.sleep", return_value=None):
        
        print("\nTesting analyze_agent_logs with auto-selection...")
        # Should auto-select Test Agent because it's the only one running
        res = analyze_agent_logs(agent_id="")
        
        print(f"Result type: {type(res)}")
        print(f"Result message: {res.get('message')}")
        
        # Verify calls
        from unittest.mock import ANY
        mock_client.list_agents.assert_called()
        mock_client.request_agent_debug_logs.assert_called_with("uuid-123", ANY, ANY)
        mock_client.get_agent_debug_logs.assert_called_with("req-456")
        
        print("\nSUCCESS: AttributeErrors resolved and logic flow confirmed.")

if __name__ == "__main__":
    asyncio.run(verify_debug_tools())
