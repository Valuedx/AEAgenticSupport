from tools.base import get_ae_client
import json

if __name__ == "__main__":
    client = get_ae_client()
    data = client.get_execution_status("2506179")
    with open("d:/AEAgenticSupport/ext_status_full.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("DONE")
