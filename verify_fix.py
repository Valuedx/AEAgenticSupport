import sys
import os
sys.path.insert(0, os.getcwd())
from config.llm_client import llm_client

text = "What workflows are having issues?"
print(f"Testing classification for: '{text}'")

# Simulate the classification logic from orchestrator.py
best_similarity = 0.032 # From our previous test
active_status = "none"

try:
    route = llm_client.chat(
        (
            "Classify this user message for routing into one of four categories.\n"
            "CRITICAL: YOUR OUTPUT MUST BE EXACTLY ONE WORD: 'ACK', 'SMALLTALK', 'GENERAL', or 'OPS'.\n"
            "DO NOT provide any explanation, preamble, or punctuation.\n\n"
            "ACK = brief acknowledgement, thank you, or closing of the conversation.\n"
            "SMALLTALK = greeting/chit-chat that does not contain a specific task or question.\n"
            "GENERAL = a general non-technical question that does not require workflows, tools, or SOPs.\n"
            "OPS = any operations, support, troubleshooting, or automation intent (e.g. asking about status, errors, workflows, or fixes).\n"
            f"Active issue status: {active_status}\n"
            f"Tool relevance score (0-1): {best_similarity:.3f}\n"
            f'User message: "{text}"'
        ),
        system="Be strict and output one token only.",
        temperature=0.0,
        max_tokens=8,
    ).strip().upper()
    print(f"Primary classification (LLM): {route!r}")
    
    if route not in {"ACK", "SMALLTALK", "GENERAL", "OPS"}:
        print("Ambiguous LLM output, falling back to similarity...")
        route = "OPS" if best_similarity >= 0.01 else "GENERAL"
        print(f"Fallback classification: {route}")
except Exception as e:
    print(f"LLM Classification failed: {e}")
    route = "OPS" if best_similarity >= 0.01 else "GENERAL"
    print(f"Error Fallback classification: {route}")

print(f"\nFINAL ROUTE: {route}")
print(f"Expected OPS? {route == 'OPS'}")
