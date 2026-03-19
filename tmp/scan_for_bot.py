import json
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from tools.automationedge_client import get_automationedge_client

def scan_for_bot():
    client = get_automationedge_client()
    target_id = 6975
    target_name = "Death_Claim"
    
    found = []
    page_size = 100
    max_pages = 10
    
    print(f"Scanning up to {page_size * max_pages} instances for ID {target_id}...")
    
    for page in range(max_pages):
        offset = page * page_size
        print(f"Fetching page {page} (offset {offset})...")
        try:
            res = client._authorized_request(
                "POST", "/workflowinstances", 
                params={"offset": offset, "size": page_size},
                payload={},
                use_rest_prefix=True,
                silent_on_status=[]
            )
            items = client._extract_list(res)
            if not items:
                break
            
            for item in items:
                # Check for ID or Name in various fields
                item_wf_id = item.get("workflowId")
                item_wf_name = (item.get("workflowName") or 
                              (item.get("workflowConfiguration") or {}).get("name") or 
                              "")
                
                if str(item_wf_id) == str(target_id) or target_name.lower() in item_wf_name.lower():
                    found.append(item)
                    print(f"FOUND MATCH! ID={item_wf_id}, Name={item_wf_name}, Status={item.get('status')}")
            
            if len(items) < page_size:
                break
        except Exception as e:
            print(f"Error on page {page}: {e}")
            break
            
    if not found:
        print(f"Bot {target_name} ({target_id}) not found in the last {offset + page_size} instances.")
    else:
        print(f"Total instances found for {target_name}: {len(found)}")

if __name__ == "__main__":
    scan_for_bot()
