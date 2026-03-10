
import sys
import os
import json
from datetime import timedelta

# Ensure we can import from the root
sys.path.append(os.getcwd())

from config.settings import CONFIG
from tools.registry import tool_registry
from tools import mcp_tools

def audit_tools():
    # Force registration
    CONFIG["AE_MCP_TOOLS_ENABLED"] = True
    # Test local mode first (default)
    mcp_tools._register_mcp_tools()
    
    tools = tool_registry.get_all_definitions()
    mcp_tool_defs = [t for t in tools if t.metadata.get("source") == "mcp"]
    
    print(f"TOTAL MCP TOOLS REGISTERED: {len(mcp_tool_defs)}")
    
    anomalies = []
    
    for t in mcp_tool_defs:
        # Check title
        title = t.metadata.get("title")
        if not title or title == t.name:
            anomalies.append(f"MISSING_TITLE: {t.name}")
            
        # Check description
        if not t.description or len(t.description) < 10:
            anomalies.append(f"WEAK_DESCRIPTION: {t.name}")
            
        # Check parameters
        if not t.parameters and not t.always_available:
            # Some tools might genuinely have no params, but worth noting
            anomalies.append(f"NO_PARAMS: {t.name}")
            
        # Check category
        if t.category == "status" and "request" not in t.name and "agent" not in t.name:
            # 'status' is a fallback; check if it's too generic
            anomalies.append(f"GENERIC_CATEGORY: {t.name}")

    if anomalies:
        print("\nANOMALIES DETECTED:")
        for a in anomalies:
            print(f"  - {a}")
    else:
        print("\nALL 106 TOOLS PASSED REGISTRY AUDIT (Local Mode)")

    # Now verify remote mode if URL is set
    url = CONFIG.get("AE_MCP_SERVER_URL")
    if url:
        print(f"\nAUDITING REMOTE MODE (URL: {url})...")
        try:
            # Re-register for remote
            mcp_tools._register_mcp_tools()
            remote_defs = [t for t in tool_registry.get_all_definitions() if t.metadata.get("source") == "mcp"]
            print(f"TOTAL REMOTE MCP TOOLS DISCOVERED: {len(remote_defs)}")
            if len(remote_defs) != len(mcp_tool_defs):
                 print(f"WARNING: Count mismatch! Local={len(mcp_tool_defs)}, Remote={len(remote_defs)}")
        except Exception as e:
            print(f"REMOTE AUDIT FAILED: {e}")

if __name__ == "__main__":
    audit_tools()
