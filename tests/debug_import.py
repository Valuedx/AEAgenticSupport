import tools.status_tools
print(f"Module file: {tools.status_tools.__file__}")

from tools.status_tools import check_workflow_status
import json

res = check_workflow_status("License_bot")
print("Result keys:", res.keys())
print("Message:", res.get("message"))
