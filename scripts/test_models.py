import sys
import os
sys.path.insert(0, os.getcwd())
from config.llm_client import llm_client
from google import genai
from google.genai import types as genai_types

# Initialize a direct client to bypass current config
project = os.environ.get("GOOGLE_CLOUD_PROJECT", "vdxexccenter")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
client = genai.Client(vertexai=True, project=project, location=location)

def test_model(model_name):
    print(f"\n--- Testing {model_name} ---")
    try:
        config = genai_types.GenerateContentConfig(
            temperature=0.5,
            max_output_tokens=500
        )
        resp = client.models.generate_content(
            model=model_name,
            contents="Hello, how are you? Tell me a bit about AutomationEdge.",
            config=config
        )
        text = "".join(p.text for c in resp.candidates for p in c.content.parts if p.text)
        print(f"Response Length: {len(text)}")
        print(f"Response: {text[:100]}...")
    except Exception as e:
        print(f"Error with {model_name}: {e}")

test_model("gemini-1.5-flash")
test_model("gemini-2.0-flash")
test_model("gemini-2.5-flash") # The one in .env
