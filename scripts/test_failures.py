import logging
import json
from tools.status_tools import list_recent_failures
from config.logging_setup import setup_logging

# Setup basic logging to see the httpx calls
setup_logging()

def test_failures():
    print("Testing list_recent_failures...")
    try:
        result = list_recent_failures(hours=24)
        print(f"Total Count: {result.get('total_count')}")
        print(f"Time Window: {result.get('time_window_hours')}h")
        if result.get('warning'):
            print(f"Warning: {result.get('warning')}")
        
        failures = result.get('failures', [])
        if failures:
            print(f"Found {len(failures)} failures:")
            for f in failures[:3]:
                print(f" - {f.get('workflow_name')} ({f.get('status')}): {f.get('error_message')[:100]}...")
        else:
            print("No failures found in the list.")
            
    except Exception as e:
        print(f"Error executing tool: {e}")

if __name__ == "__main__":
    test_failures()
