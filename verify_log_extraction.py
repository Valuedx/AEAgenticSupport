
import sys
import os
import io
import zipfile
import time
from unittest.mock import MagicMock, patch

# Force UTF-8 for stdout
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Create a sample ZIP in memory
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    z.writestr("agent.log", "2026-03-16 21:00:00 [INFO] Starting agent...\n2026-03-16 21:05:00 [ERROR] Connection lost!")
zip_content = buf.getvalue()

# Mock AE Client
class MockAEClient:
    def __init__(self):
        self.default_org_code = "vdx"
    def check_agent_status(self):
        return [{"uuid": "agent-123", "agentName": "test-agent", "agentState": "RUNNING"}]
    def request_agent_debug_logs(self, uuid, start, end):
        return {"id": 1289}
    def get_debug_log_request(self, req_id):
        # Simulate returning the ZIP immediately upon first poll
        return {"is_zip": True, "log_zip_content": zip_content}
    def _authorized_request(self, method, path, **kwargs):
        return {"is_zip": True, "log_zip_content": zip_content}

# Mock the get_ae_client utility
with patch("tools.agent_debug_tools.get_ae_client", return_value=MockAEClient()):
    # Mock time.sleep to speed up test
    with patch("time.sleep", return_value=None):
        from tools.agent_debug_tools import analyze_agent_logs
        
        print("Testing analyze_agent_logs with direct ZIP response...")
        result = analyze_agent_logs("test-agent")
        
        print("\n--- RESULT ---")
        print(f"Success: {result['success']}")
        if result['success']:
            print(f"Message: {result['message']}")
            print(f"Errors Found: {result['error_found']}")
            for log in result['logs']:
                print(f"File: {log['filename']}")
                print(f"Tail Content:\n{log['content']}")
        else:
            print(f"Error: {result['error']}")

        if result['success'] and result['error_found'] and len(result['logs']) > 0:
            print("\nVERIFICATION SUCCESSFUL: ZIP was processed correctly during polling.")
        else:
            print("\nVERIFICATION FAILED.")
