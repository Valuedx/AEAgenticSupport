import sys
import os

# Add root directory to sys.path
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

from tools.registry import tool_registry
import tools.log_tools # Trigger registration
import tools.agent_debug_tools # Trigger registration

def verify_log_tools():
    print("Verifying Log Tool Definitions...")
    
    # Check get_execution_logs
    t1 = tool_registry.get_tool("get_execution_logs")
    print(f"\nTool: {t1.name}")
    print(f"Description includes 'SPECIFIC': {'SPECIFIC' in t1.description}")
    print(f"Description includes 'NOT require a date': {'NOT require a date' in t1.description}")
    print(f"Avoid When: {t1.avoid_when}")
    
    # Check analyze_agent_logs
    t2 = tool_registry.get_tool("analyze_agent_logs")
    print(f"\nTool: {t2.name}")
    print(f"Description includes 'AGENT': {'AGENT' in t2.description}")
    print(f"Avoid When: {t2.avoid_when}")

    print("\nVerification Complete.")

if __name__ == "__main__":
    verify_log_tools()
