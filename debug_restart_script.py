import sys
import json
from tools.remediation_tools import restart_execution

if __name__ == "__main__":
    ext_id = "2506179"
    workflow_name = "Email_Bot_JD"
    resp = restart_execution(execution_id=ext_id, workflow_name=workflow_name)
    with open("d:/AEAgenticSupport/debug_restart.json", "w") as f:
        json.dump(resp, f, indent=2)
    print("DONE")
