import logging
import sys
import os
import httpx

# Set up paths to include current directory
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("probe_t4")

def main():
    client = get_automationedge_client()
    
    # We want to test /workflowinstances with POST
    paths = [
        "/workflowinstances",
        f"/{client.default_org_code}/workflowinstances" if client.default_org_code else None,
    ]
    paths = [p for p in paths if p]
    
    payload = {
        "offset": 0,
        "size": 10,
        "order": "desc",
        "workflowName": "License_bot"
    }

    for path in paths:
        for use_prefix in [True, False]:
            full_path = client._rest_path(path) if use_prefix else path
            logger.info(f"Probing POST {full_path} (use_prefix={use_prefix})...")
            try:
                resp = client._authorized_request("POST", path, payload=payload, use_rest_prefix=use_prefix)
                logger.info(f"SUCCESS on {full_path}")
                # logger.info(f"Result: {resp}")
                return
            except Exception as e:
                logger.warning(f"FAILED on {full_path}: {e}")

if __name__ == "__main__":
    main()
