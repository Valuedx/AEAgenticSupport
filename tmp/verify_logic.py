import re

def mock_resolve_logic(workflow_name):
    """
    Simulated version of the logic in AutomationEdgeClient.resolve_cached_workflow_name
    to verify that regexes and variants are built correctly.
    """
    name = str(workflow_name or "").strip()
    if not name:
        return []

    variants = []
    variants.append(name)
    
    # Strip ID suffix: "My Workflow (ID: 101)" -> "My Workflow"
    no_id = re.sub(r"\s*\(ID:\s*\d+\)\s*$", "", name, flags=re.IGNORECASE).strip()
    if no_id and no_id != name:
        variants.append(no_id)

    # Slug variants
    base = name.replace("-", "_").replace(" ", "_").strip()
    if base and base != name and base != no_id:
        variants.append(base)

    clean_query = re.sub(r"[^a-zA-Z0-9]", "%", no_id or name)
    
    return {
        "variants": variants,
        "fuzzy_query": f"%{clean_query.lower()}%"
    }

test_cases = [
    "Cashier Receipting Report Download -MG22P1W25",
    "Cashier Receipting Report Download -MG22P1W25 (ID: 12345)",
    "Workflow-With-ID (ID: 999)",
    "Small Name"
]

print("--- Logic Verification Results ---")
for t in test_cases:
    res = mock_resolve_logic(t)
    print(f"Input: '{t}'")
    print(f"  Variants for Exact Match: {res['variants']}")
    print(f"  Fallback Fuzzy Query: {res['fuzzy_query']}")
    print("-" * 30)
