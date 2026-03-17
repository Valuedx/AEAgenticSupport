import sys
import logging
from tools.log_tools import get_execution_logs

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    ext_id = "2506179"
    try:
        logs_resp = get_execution_logs(ext_id)
        if logs_resp.get("logs"):
            print(f"SUCCESS: Retrieved {len(logs_resp['logs'])} logs.")
            print(logs_resp['logs'][:5])
        else:
            print("FAILED:", logs_resp)
    except Exception as e:
        print(f"ERROR: {e}")
