
import os
import sys
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_bot_auth")

def test_auth():
    env_path = r"d:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\.env"
    print(f"Reading .env from {env_path}")
    
    # Simple .env parser
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip("'").strip('"')
    else:
        print("Error: .env file not found!")
        return

    # Add project to path to import common.http_utils
    project_root = r"d:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        import common.http_utils
        print(f"Modue members: {dir(common.http_utils)}")
        from common.http_utils import http_post
        print("Successfully imported http_post from common.http_utils")
    except ImportError as e:
        print(f"Error importing http_post: {e}")
        # Try to find something similar
        import common.http_utils
        members = dir(common.http_utils)
        similar = [m for m in members if "http" in m.lower()]
        print(f"Similar members found: {similar}")
        return
    except Exception as e:
        print(f"Unexpected error during import: {e}")
        return

    # Mock parameters for MS Bot authentication
    # Usually MS Bot auth goes to https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token
    ms_app_id = env_vars.get("MS_APP_ID")
    ms_app_password = env_vars.get("MS_APP_PASSWORD")
    
    url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": ms_app_id,
        "client_secret": ms_app_password,
        "scope": "https://api.botframework.com/.default"
    }
    
    print(f"Attempting http_post to {url}")
    try:
        # The error happens here: TypeError: __init__() got an unexpected keyword argument 'proxies'
        # We suspect http_post calls something (like requests.Session or a custom class) 
        # and passes 'proxies' incorrectly.
        
        # In many versions of this codebase, http_post takes (url, data, headers, proxies, timeout, etc.)
        # If we don't pass proxies, does it still fail? 
        # The traceback from user showed it failed during "Authentication fail with MS Bot"
        
        response = http_post(url, data=payload)
        print(f"Response status: {response.status_code if hasattr(response, 'status_code') else 'Unknown'}")
        print(f"Response text: {response.text if hasattr(response, 'text') else 'No text'}")
    except TypeError as e:
        print(f"Caught expected TypeError: {e}")
    except Exception as e:
        print(f"Caught unexpected exception: {e}")

if __name__ == "__main__":
    test_auth()
