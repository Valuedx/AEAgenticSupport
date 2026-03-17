import sys
import logging
from tools.remediation_tools import restart_execution

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    ext_id = "2506179"
    workflow_name = "Email_Bot_JD"
    try:
        print(f"Testing restart execution for {workflow_name} with ID {ext_id}")
        resp = restart_execution(execution_id=ext_id, workflow_name=workflow_name)
        print("====== FULL RESPONSE ======")
        print(repr(resp))
        print("===========================")
    except Exception as e:
        print(f"ERROR: {e}")
