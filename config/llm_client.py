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

logger = logging.getLogger("ops_agent.llm")

class VertexAIClient:
    """LLM client using the newer google-genai SDK (v3)."""

    def __init__(self):
        self.project = CONFIG["GOOGLE_CLOUD_PROJECT"]
        self.location = CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.model_name = CONFIG.get("VERTEX_AI_MODEL", "gemini-2.0-flash")
        
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
        return response

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
