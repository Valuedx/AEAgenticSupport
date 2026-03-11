
import sys
import os
# Add parent directory to path to import tools
sys.path.append(os.getcwd())

from tools.registry import tool_registry
import tools.remediation_tools # Ensure they are registered

def verify_tiers():
    tools_to_check = ["restart_execution", "resubmit_execution", "trigger_workflow"]
    all_passed = True
    
    print("--- Tool Tier Verification ---")
    for tool_name in tools_to_check:
        tool_def = tool_registry.get_tool(tool_name)
        if not tool_def:
            print(f"[FAIL] {tool_name} not found in registry")
            all_passed = False
            continue
            
        tier = tool_def.tier
        print(f"Tool: {tool_name} | Tier: {tier}")
        
        if tier != "medium_risk":
            print(f"  [ERROR] {tool_name} should be 'medium_risk' but is '{tier}'")
            all_passed = False
        else:
            print(f"  [PASS] {tool_name} is correctly set to 'medium_risk'")
            
    if all_passed:
        print("\nOVERALL: SUCCESS - All mutation tools are now safe-guarded.")
    else:
        print("\nOVERALL: FAILURE - Some tools are still at low risk.")

if __name__ == "__main__":
    verify_tiers()
