import logging
import threading
import typing
from typing import Optional

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
from config.observability import create_generation
from state.app_config import get_runtime_value

_active_trace: threading.local = threading.local()

logger = logging.getLogger("ops_agent.llm")


def set_current_trace(trace) -> None:
    """Set the LangFuse trace for the current thread (called by orchestrator)."""
    _active_trace.trace = trace


def get_current_trace():
    """Return the LangFuse trace for the current thread, or None."""
    return getattr(_active_trace, "trace", None)


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
        text = self._extract_text(resp)
        self._record_generation(
            name="chat",
            input={"prompt": prompt[:500], "system": system[:300]},
            output=text[:1000],
            resp=resp,
        )
        return text

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

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=messages,
            config=config
        )
        
        # Track tokens
        self._record_usage(response)

        tool_names = self._extract_tool_call_names(response)
        output_text = self._extract_text(response)
        self._record_generation(
            name="chat_with_tools",
            input={"message_count": len(messages), "tool_count": len(tools)},
            output={"text": output_text[:500], "tool_calls": tool_names} if tool_names else output_text[:1000],
            resp=response,
            metadata={"tool_calls": tool_names} if tool_names else None,
        )

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

    def _record_generation(self, name: str, input: typing.Any,
                           output: typing.Any, resp: typing.Any,
                           metadata: Optional[dict] = None) -> None:
        """Send an LLM generation event to LangFuse (non-blocking)."""
        trace = get_current_trace()
        if trace is None:
            return
        usage_details = None
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            u = resp.usage_metadata
            usage_details = {
                "input": u.prompt_token_count or 0,
                "output": u.candidates_token_count or 0,
                "total": u.total_token_count or 0,
            }
        create_generation(
            trace,
            name=name,
            model=self.model_name,
            input=input,
            output=output,
            usage_details=usage_details,
            metadata=metadata,
        )

    @staticmethod
    def _extract_tool_call_names(resp) -> list[str]:
        """Return tool/function names from a tool-calling response."""
        names: list[str] = []
        for candidate in getattr(resp, "candidates", []):
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []):
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    names.append(fc.name)
        return names

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



class _LazyLLMClient:
    """Proxy that defers VertexAIClient construction to first use.

    This prevents credential / env-var errors at import time for scripts
    (e.g. run_rag_index.py) that import tool modules but never actually
    invoke the LLM.
    """
    _instance: Optional["VertexAIClient"] = None

    def _get(self) -> "VertexAIClient":
        if self._instance is None:
            self._instance = VertexAIClient()
        return self._instance

    def chat(self, *args, **kwargs):
        return self._get().chat(*args, **kwargs)

    def chat_with_tools(self, *args, **kwargs):
        return self._get().chat_with_tools(*args, **kwargs)

    def _record_usage(self, *args, **kwargs):
        return self._get()._record_usage(*args, **kwargs)

    def _extract_text(self, *args, **kwargs):
        return self._get()._extract_text(*args, **kwargs)


llm_client = _LazyLLMClient()


def reset_llm_client() -> VertexAIClient:
    global llm_client
    llm_client._instance = VertexAIClient()  # type: ignore[union-attr]
    return llm_client._instance               # type: ignore[return-value]
