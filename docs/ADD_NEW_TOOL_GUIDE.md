# Developer Guide: Adding New Tools (Step-by-Step)

This guide is designed for developers (including freshers) to understand exactly where and how to add new capabilities to the AEAgenticSupport system.

There are two main ways to add a tool:
1. **Native Framework Tools**: Best for general helper scripts and system lookups.
2. **MCP (Model Context Protocol) Tools**: Best for AutomationEdge-specific operations and standardized external integrations.

---

## Path A: Adding a Native Framework Tool

Native tools are Python functions registered directly within the core application.

### Exact File Locations
* **Default Directory**: `tools/`
* **Default Recommended File**: `tools/general_tools.py` (or create a new specialized file in `tools/`)

### Step-by-Step Implementation

#### 1. Add your logic and registration
Open `tools/general_tools.py` and scroll to the bottom. Add your function and its registration block.

```python
# FILE: tools/general_tools.py

# 1. Define the logic (What the tool actually DOES)
def get_custom_system_info(category: str = "all") -> dict:
    """Returns specialized system information for the specified category."""
    # Example logic: returning a mock response
    return {
        "category": category,
        "status": "active",
        "data_points": 42
    }

# 2. Register the tool (Telling the AI about the tool)
from tools.base import ToolDefinition
from tools.registry import tool_registry

tool_registry.register(
    ToolDefinition(
        name="get_custom_system_info",
        # This description is what the AI reads to decide IF it should use this tool.
        description="Fetch specialized system diagnostic information based on a category.",
        category="general",
        tier="read_only",  # 'read_only' is safe; 'medium_risk' requires user approval.
        parameters={
            "category": {
                "type": "string",
                "description": "The category of info to fetch (e.g., 'network', 'storage').",
            }
        },
        required_params=[],
        # 'use_when' helps the AI understand the context.
        use_when="The user asks for specific system-level metrics or diagnostic data.",
        input_examples=[{"category": "network"}]
    ),
    get_custom_system_info  # Connect the metadata to the actual function above.
)
```

#### 2. Ensure the module is loaded (Only if you created a NEW file)
If you added your tool to an **existing** file like `tools/general_tools.py`, you are done! 
If you created a **new file** (e.g., `tools/my_new_tools.py`), you must register that file in the bootstrap:

* **File to modify**: `tools/bootstrap.py`
* **Variable**: `_STATIC_TOOL_MODULES`

```python
# FILE: tools/bootstrap.py
_STATIC_TOOL_MODULES = [
    # ... existing modules ...
    "tools.my_new_tools",  # <--- Add your new file here (use dots instead of slashes)
]
```

---

## Path B: Adding an MCP (Model Context Protocol) Tool

MCP tools follow a stricter protocol and are better for tools that might be shared across different AI clients.

### Exact File Locations
* **Logic Directory**: `mcp_server/tools/`
* **Metadata/Registry File**: `mcp_server/tool_specs.py`

### Step-by-Step Implementation

#### 1. Add the logic
Create or open a file in `mcp_server/tools/` (e.g., `mcp_server/tools/misc_tools.py`).

```python
# FILE: mcp_server/tools/misc_tools.py

async def ping_external_service(service_name: str) -> dict:
    """Ping an external service and return its latency."""
    return {"service": service_name, "status": "online", "latency_ms": 45}
```

#### 2. Register the "Spec"
Open `mcp_server/tool_specs.py` and modify the `get_mcp_tool_specs` function.

```python
# FILE: mcp_server/tool_specs.py

def get_mcp_tool_specs() -> tuple[MCPToolSpec, ...]:
    from mcp_server.tools import misc_tools as _misc # 1. Import your logic file
    
    return (
        # ... existing tools ...
        # 2. Add your tool to this long list
        _spec("ae.misc.ping_service", _misc.ping_external_service, "platform_read", "safe_read"),
    )
```

#### 3. Add Human-Readable Polish (Optional but Recommended)
In the same file (`mcp_server/tool_specs.py`), find the `_CURATED_TOOL_OVERRIDES` dictionary to add a nice title and examples.

```python
# FILE: mcp_server/tool_specs.py

_CURATED_TOOL_OVERRIDES = {
    # Add your tool name as a key here:
    "ae.misc.ping_service": {
        "title": "Misc: Ping External Service",
        "description": "Check if an external service is reachable and measure response time.",
        "use_when": "Troubleshooting connectivity issues with external vendors.",
        "input_examples": [{"service_name": "google.com"}],
    },
}
```

---

## Quick Reference Table for Freshers

| Task | File Path (Relative to Root) | Purpose |
| :--- | :--- | :--- |
| **Implement Native Logic** | `tools/general_tools.py` | Where the Python code for your tool lives. |
| **Register Native Tool** | `tools/general_tools.py` | Linking the function to the `tool_registry`. |
| **Manage Startup** | `tools/bootstrap.py` | Ensures your tool files are loaded when the app starts. |
| **Implement MCP Logic** | `mcp_server/tools/*.py` | Where the MCP version of the action lives. |
| **Register MCP Tool** | `mcp_server/tool_specs.py` | Defining the tool name and category for MCP clients. |

### Pro-Tips for Success:
1. **Safety First**: If your tool deletes something or changes a password, use `tier="medium_risk"` or `safety="guarded"`. This forces the AI to ask the user for permission first.
2. **Clear Descriptions**: The AI "reads" your description to know if the tool is relevant. Be very clear!
3. **Restarts**: After adding a tool, you **must restart the server** for the changes to take effect and for the new tool to be indexed in the AI's search engine (RAG).
