
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.engine import get_rag_engine

def search_rogue_sops():
    rag = get_rag_engine()
    print("--- Searching for 'Start Date' in SOPs ---")
    hits = rag.search_sops("Start Date", top_k=10)
    for hit in hits:
        print(f"ID: {hit['id']} | Rank: {hit.get('rrf_score') or hit.get('similarity')}")
        print(f"Content: {hit['content'][:300]}...")
        print("-" * 20)

    print("\n--- Searching for 'Any other relevant information' in SOPs ---")
    hits = rag.search_sops("Any other relevant information", top_k=10)
    for hit in hits:
        print(f"ID: {hit['id']} | Rank: {hit.get('rrf_score') or hit.get('similarity')}")
        print(f"Content: {hit['content'][:300]}...")
        print("-" * 20)

if __name__ == "__main__":
    search_rogue_sops()
