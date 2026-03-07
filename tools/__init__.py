from tools.registry import tool_registry

import tools.status_tools      # noqa: F401
import tools.log_tools         # noqa: F401
import tools.file_tools        # noqa: F401
import tools.remediation_tools # noqa: F401
import tools.dependency_tools  # noqa: F401
import tools.notification_tools # noqa: F401
import tools.general_tools     # noqa: F401
import tools.rca_tools         # noqa: F401
import tools.mcp_tools         # noqa: F401 — registers MCP P0 tools when AE_MCP_TOOLS_ENABLED=true
