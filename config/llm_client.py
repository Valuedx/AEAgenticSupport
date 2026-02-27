"""
LLM client using Google Vertex AI (Gemini models).
Supports both on-prem service account auth and Workload Identity.
"""

import logging

from tenacity import retry, stop_after_attempt, wait_exponential
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

from config.settings import CONFIG

logger = logging.getLogger("ops_agent.llm")

vertexai.init(
    project=CONFIG["GOOGLE_CLOUD_PROJECT"],
    location=CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
)


class VertexAIClient:

    def __init__(self):
        self.model_name = CONFIG.get("VERTEX_AI_MODEL", "gemini-2.0-flash")
        self.model = GenerativeModel(self.model_name)
        self.gen_config = GenerationConfig(
            temperature=0.1,
            max_output_tokens=4096,
            top_p=0.95,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def chat(self, prompt: str, system: str = "",
             temperature: float = None, max_tokens: int = None) -> str:
        config = GenerationConfig(
            temperature=temperature or self.gen_config.temperature,
            max_output_tokens=max_tokens or self.gen_config.max_output_tokens,
            top_p=0.95,
        )
        model = GenerativeModel(
            self.model_name,
            system_instruction=system if system else None,
        )
        response = model.generate_content(prompt, generation_config=config)
        return response.text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def chat_with_tools(self, messages: list[dict], tools: list[dict],
                        system: str = "") -> dict:
        model = GenerativeModel(
            self.model_name,
            system_instruction=system if system else None,
        )
        response = model.generate_content(
            messages,
            tools=tools,
            generation_config=self.gen_config,
        )
        return response


llm_client = VertexAIClient()
