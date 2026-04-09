import logging
import typing

from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai.errors import ClientError as GenaiClientError
except ImportError:
    # Fallback for environments where it's not installed yet
    logger = logging.getLogger("ops_agent.llm")
    logger.error("google-genai not installed. Run: pip install google-genai")
    raise

from config.settings import CONFIG
from config.metrics import metrics_collector, TokenUsage
from state.app_config import get_runtime_value

logger = logging.getLogger("ops_agent.llm")
_VERTEX_MAX_OUTPUT_TOKENS_CAP = 65536


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """Return True for errors worth retrying, especially 429 rate limits.

    The google-genai SDK has its own internal tenacity retry.  When that
    exhausts, it raises ``tenacity.RetryError`` wrapping the real
    ``ClientError``.  We must unwrap it to detect the 429 status code.
    """
    inner = exc
    # Unwrap tenacity.RetryError → inner ClientError
    if isinstance(exc, RetryError):
        last = getattr(exc, "last_attempt", None)
        if last is not None:
            try:
                inner = last.result()
            except BaseException as real_exc:
                inner = real_exc

    if isinstance(inner, GenaiClientError):
        status = getattr(inner, "status_code", None) or getattr(inner, "code", None)
        if status == 429:
            logger.warning(
                "429 RESOURCE_EXHAUSTED from Vertex AI — will retry with backoff"
            )
            return True
        # Also check the error message for 429 / RESOURCE_EXHAUSTED
        err_text = str(inner).lower()
        if "429" in err_text or "resource_exhausted" in err_text:
            logger.warning(
                "RESOURCE_EXHAUSTED detected in error text — will retry with backoff"
            )
            return True

    # Check string representation as last resort (covers wrapped exceptions)
    err_str = str(exc).lower()
    if "429" in err_str or "resource_exhausted" in err_str:
        logger.warning(
            "429/RESOURCE_EXHAUSTED detected in exception chain — will retry with backoff"
        )
        return True

    # Also retry generic transient / connection errors
    return isinstance(exc, (ConnectionError, TimeoutError))


# 429-aware retry: up to 5 attempts, exponential backoff 2s -> 60s.
# The longer max gives Vertex AI quota time to recover.
_LLM_RETRY = retry(
    retry=retry_if_exception(_is_retryable_llm_error),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)


class VertexAIClient:
    """LLM client using the newer google-genai SDK (v3)."""

    def __init__(self):
        self.project = CONFIG["GOOGLE_CLOUD_PROJECT"]
        self.location = get_runtime_value(
            "VERTEX_AI_LOCATION",
            CONFIG.get(
                "VERTEX_AI_LOCATION",
                CONFIG.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            ),
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
        configured_max_tokens = int(CONFIG.get("VERTEX_AI_MAX_TOKENS", 65536) or 65536)
        self.default_max_tokens = min(configured_max_tokens, _VERTEX_MAX_OUTPUT_TOKENS_CAP)

    @staticmethod
    def _reduced_output_tokens(token_count: int) -> int:
        current = max(int(token_count or 0), 1)
        if current <= 1024:
            return current
        return max(1024, current // 2)

    @staticmethod
    def _reduce_prompt_for_retry(prompt: str, max_chars: int = 6000) -> str:
        text = str(prompt or "")
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head
        return (
            text[:head]
            + "\n...[truncated for rate-limit retry]...\n"
            + text[-tail:]
        )

    @classmethod
    def _reduce_contents_for_retry(
        cls,
        contents: list,
        *,
        keep_last: int = 6,
        max_text_chars: int = 2000,
    ) -> list:
        recent = list(contents[-keep_last:]) if len(contents) > keep_last else list(contents)
        reduced = []
        for item in recent:
            role = getattr(item, "role", None)
            parts = getattr(item, "parts", None)
            if not role or not isinstance(parts, list):
                reduced.append(item)
                continue

            new_parts = []
            for part in parts:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    shrunk = cls._reduce_prompt_for_retry(text, max_chars=max_text_chars)
                    new_parts.append(genai_types.Part(text=shrunk))
                    continue
                new_parts.append(part)
            reduced.append(genai_types.Content(role=role, parts=new_parts))
        return reduced

    @_LLM_RETRY
    def chat(self, prompt: str, system: str = "",
             temperature: float = None, max_tokens: int = None) -> str:
        """Simple text generation with optional system prompt."""
        output_tokens = max_tokens or self.default_max_tokens
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
            max_output_tokens=output_tokens,
            top_p=0.95,
        )

        try:
            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )
        except BaseException as exc:
            if not _is_retryable_llm_error(exc):
                raise

            reduced_prompt = self._reduce_prompt_for_retry(prompt)
            reduced_output_tokens = self._reduced_output_tokens(output_tokens)
            logger.warning(
                "Retrying Vertex chat with reduced input/output: prompt_chars=%d->%d max_output_tokens=%d->%d",
                len(str(prompt or "")),
                len(reduced_prompt),
                int(output_tokens),
                int(reduced_output_tokens),
            )
            retry_contents = [
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=reduced_prompt)],
                )
            ]
            retry_config = genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature or self.default_temp,
                max_output_tokens=reduced_output_tokens,
                top_p=0.95,
            )
            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=retry_contents,
                config=retry_config,
            )
        
        # Track tokens
        self._record_usage(resp)

        # Extract text from the new response structure
        return self._extract_text(resp)

    @_LLM_RETRY
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

        try:
            # The SDK handles the message list (list of Content objects) directly
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=messages,
                config=config
            )
        except BaseException as exc:
            if not _is_retryable_llm_error(exc):
                raise

            reduced_messages = self._reduce_contents_for_retry(messages)
            reduced_output_tokens = self._reduced_output_tokens(self.default_max_tokens)
            logger.warning(
                "Retrying Vertex tool chat with reduced input/output: messages=%d->%d max_output_tokens=%d->%d",
                len(messages),
                len(reduced_messages),
                int(self.default_max_tokens),
                int(reduced_output_tokens),
            )
            retry_config = genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=self.default_temp,
                max_output_tokens=reduced_output_tokens,
                tools=tools,
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=reduced_messages,
                config=retry_config
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



class _LazyLLMClient:
    """Proxy that defers VertexAIClient construction to first use.

    This prevents credential / env-var errors at import time for scripts
    (e.g. run_rag_index.py) that import tool modules but never actually
    invoke the LLM.
    """
    _instance: "VertexAIClient | None" = None

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
