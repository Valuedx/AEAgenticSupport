from tools.bootstrap import initialize_tooling
from tools.registry import tool_registry

initialize_tooling()
tools = tool_registry.list_tools()
for t in sorted(tools):
    if "status" in t or "request" in t or "execution" in t:
        print(t)
