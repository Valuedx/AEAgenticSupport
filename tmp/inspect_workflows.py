import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.orchestrator_client import get_orchestrator_client

client = get_orchestrator_client()
workflows = client.list_workflows()
print(json.dumps(workflows, indent=2))
