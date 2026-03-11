
import os
import sys
import json

# Ensure local imports work
sys.path.insert(0, os.getcwd())

from tools.bootstrap import initialize_tooling
from tools.registry import tool_registry
from rag.engine import get_rag_engine
from mcp_server.ae_client import get_ae_client
import logging

# Configure logging to see tool debug messages
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

def resolve_placeholders(args):
    """If args contain dummy values like 'DUMMY' or are missing, try to fetch real ones."""
    client = get_ae_client()
    
    if args.get("agent_id") == "DUMMY" or not args.get("agent_id"):
        try:
            agents = client.list_agents()
            if agents:
                # Use the first RUNNING/CONNECTED agent if possible, else just the first one
                running = [a for a in agents if (a.get("agentState") or a.get("state") or "").upper() in ("CONNECTED", "RUNNING", "ACTIVE")]
                target = running[0] if running else agents[0]
                args["agent_id"] = target.get("agentId") or target.get("id")
                print(f"DEBUG: Resolved agent_id to '{args['agent_id']}'")
        except:
            pass
            
    if args.get("workflow_id") == "DUMMY" or not args.get("workflow_id"):
        try:
            workflows = client.get_workflows()
            if workflows:
                args["workflow_id"] = workflows[0].get("id") or workflows[0].get("workflowId")
                print(f"DEBUG: Resolved workflow_id to '{args['workflow_id']}'")
        except:
            pass

    if args.get("request_id") == "DUMMY" or not args.get("request_id"):
        try:
            # Fetch recent requests
            requests = client.search_requests(limit=1)
            if requests:
                args["request_id"] = requests[0].get("id") or requests[0].get("automationRequestId")
                print(f"DEBUG: Resolved request_id to '{args['request_id']}'")
        except:
            pass
            
    return args

def test_nl_query_and_execute(query, expected_tool=None, tool_args=None):
    print(f"\nQUERY: \"{query}\"")
    
    # Simulate the RAG + Ranking flow
    rag_hits = get_rag_engine().search_tools(query, top_k=15)
    
    # Get candidates via registry ranking
    candidates = tool_registry.rank_tool_candidates(query, rag_hits=rag_hits, top_k=5)
    
    print("TOP CANDIDATES:")
    selected_tool = None
    match_rank = -1
    for i, candidate in enumerate(candidates, 1):
        name = candidate["name"]
        score = candidate.get("score", "N/A")
        print(f"{i}. {name} (Score: {score})")
        if expected_tool and name == expected_tool:
            print(f"   => MATCHED EXPECTED TOOL at rank {i}")
            selected_tool = name
            match_rank = i
        elif not expected_tool and i == 1:
            selected_tool = name
            match_rank = i
    
    if expected_tool and not selected_tool:
        print(f"   => FAILED: Expected tool '{expected_tool}' not in top candidates.")
        return False

    if selected_tool:
        print(f"\nExecuting tool: {selected_tool}")
        args = tool_args or {}
        
        # Resolve dummies for execution
        args = resolve_placeholders(args)
        
        print(f"Arguments: {args}")
        
        try:
            result = tool_registry.execute(selected_tool, **args)
            print("\nTOOL RESULT:")
            # ToolResult likely has success, error, data attributes
            result_data = {
                "success": result.success,
                "error": result.error,
                "data": getattr(result, "data", None)
            }
            print(json.dumps(result_data, indent=2))
            
            if not result.success:
                print(f"\n[!]Tool execution reported FAILURE: {result.error}")
                return False
            return True
        except Exception as e:
            print(f"\n[!]Exception during tool execution: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_nl_tool_selection.py \"your question\" [expected_tool_name] [args_json_or_file_or_kv...]")
        sys.exit(1)
    
    initialize_tooling()
    
    query = sys.argv[1]
    expected = sys.argv[2] if len(sys.argv) > 2 else None
    
    args = {}
    if len(sys.argv) > 3:
        raw_args = sys.argv[3]
        if raw_args.startswith("{"):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                # Try fixing common PowerShell escaping issues
                try:
                    cleaned = raw_args.replace('\\"', '"')
                    args = json.loads(cleaned)
                except:
                    print(f"Error: Invalid JSON for arguments: {raw_args}")
        elif os.path.exists(raw_args):
            with open(raw_args) as f:
                args = json.load(f)
        else:
            # Handle key=value pairs
            for kv in sys.argv[3:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    # Try to infer types
                    if v.lower() == "true": v = True
                    elif v.lower() == "false": v = False
                    elif v.isdigit(): v = int(v)
                    args[k] = v
    
    test_nl_query_and_execute(query, expected, args)
