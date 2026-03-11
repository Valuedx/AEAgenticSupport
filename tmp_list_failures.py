import os
import sys
import json
from datetime import datetime, timezone

# Add the current directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.status_tools import list_recent_failures

try:
    result = list_recent_failures(hours=24, limit=20)
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error: {e}")
