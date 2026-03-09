import sys
import os
sys.path.insert(0, os.getcwd())
from rag.engine import get_rag_engine

text = "What workflows are having issues?"
print(f"Query: {text}")

try:
    rag = get_rag_engine()
    query_vec = rag.embed_query(text)
    tool_hits = rag.search_tools(text, top_k=5, query_embedding=query_vec)
    
    best_similarity = 0.0
    for hit in tool_hits or []:
        sim = float(hit.get("rrf_score", hit.get("similarity", 0.0)) or 0.0)
        print(f"Hit: {hit.get('metadata', {}).get('tool_name')} | Score: {sim:.3f}")
        best_similarity = max(best_similarity, sim)
        
    print(f"\nBest Similarity: {best_similarity:.3f}")
    print(f"Current Threshold: 0.520")
    print(f"Would match OPS? {best_similarity >= 0.520}")
except Exception as e:
    print(f"Error: {e}")
