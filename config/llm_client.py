import logging
import typing

from tenacity import retry, stop_after_attempt, wait_exponential
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    # Fallback for environments where it's not installed yet
    logger = logging.getLogger("ops_agent.llm")
    logger.error("google-genai not installed. Run: pip install google-genai")
    raise

from config.settings import CONFIG
from config.metrics import metrics_collector, TokenUsage
from state.app_config import get_runtime_value

logger = logging.getLogger("ops_agent.llm")

class VertexAIClient:
    """LLM client using the newer google-genai SDK (v3)."""

    def __init__(self):
        self.project = CONFIG["GOOGLE_CLOUD_PROJECT"]
        self.location = get_runtime_value(
            "GOOGLE_CLOUD_LOCATION",
            CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        self.model_name = get_runtime_value(
            "VERTEX_AI_MODEL",
            CONFIG.get("VERTEX_AI_MODEL", "gemini-2.0-flash"),
        )
        
        # Initialize the GenAI client
        self.client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location
        )
        
        self.default_temp = 0.1
        self.default_max_tokens = 4096

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def chat(self, prompt: str, system: str = "",
             temperature: float = None, max_tokens: int = None) -> str:
        """Simple text generation with optional system prompt."""
        contents = [genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])]
        
        system_instruction = None
        if system:
            system_instruction = genai_types.Content(
                role="system", 
                parts=[genai_types.Part(text=system)]
            )

        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature or self.default_temp,
            max_output_tokens=max_tokens or self.default_max_tokens,
            top_p=0.95,
        )

        resp = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config
        )
        
        # Track tokens
        self._record_usage(resp)

        # Extract text from the new response structure
        return self._extract_text(resp)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def chat_with_tools(self, messages: list, tools: list,
                         system: str = "") -> typing.Any:
        """Chat loop with tool supporting function declarations."""
        system_instruction = None
        if system:
            system_instruction = genai_types.Content(
                role="system", 
                parts=[genai_types.Part(text=system)]
            )

        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.default_temp,
            max_output_tokens=self.default_max_tokens,
            tools=tools,
        )

        # The SDK handles the message list (list of Content objects) directly
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=messages,
            config=config
        )
        
        # Track tokens
        self._record_usage(response)
        
        return response

    def _record_usage(self, resp):
        """Extract and record token usage if available."""
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            u = resp.usage_metadata
            usage = TokenUsage(
                prompt_tokens=u.prompt_token_count or 0,
                candidate_tokens=u.candidates_token_count or 0,
                total_tokens=u.total_token_count or 0
            )
            # In a multi-agent system, we usually have a 'current_turn' context.
            # For now, we update the latest turn if it's active.
            with metrics_collector._lock:
                if metrics_collector.active_turns:
                    # Arbitrarily pick the latest turn for now, or we'd need turn_id context here.
                    tid = list(metrics_collector.active_turns.keys())[-1]
                    metric = metrics_collector.active_turns[tid]
                    metric.token_usage.prompt_tokens += usage.prompt_tokens
                    metric.token_usage.candidate_tokens += usage.candidate_tokens
                    metric.token_usage.total_tokens += usage.total_tokens

    def _extract_text(self, resp) -> str:
        """Helper to extract text from Candidate parts."""
        buf = []
        for candidate in getattr(resp, "candidates", []):
            content = getattr(candidate, "content", None)
            if not content: continue
            for part in getattr(content, "parts", []):
                t = getattr(part, "text", None)
                if t:
                    buf.append(str(t))
        return "".join(buf)


llm_client = VertexAIClient()


def reset_llm_client() -> VertexAIClient:
    global llm_client
    llm_client = VertexAIClient()
    return llm_client
