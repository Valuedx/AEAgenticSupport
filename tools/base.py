"""
Base classes for tools and the AutomationEdge REST API client.
"""

import logging
import urllib3
from dataclasses import dataclass, field

import httpx

from config.settings import CONFIG

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("ops_agent.tools")


@dataclass
class ToolDefinition:
    """Definition of a single tool available to the agent."""
    name: str
    description: str
    category: str       # status, logs, file, remediation, dependency, notification
    tier: str           # read_only, low_risk, medium_risk, high_risk
    parameters: dict = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)
    protected_workflows: list[str] = field(default_factory=list)

    def to_rag_document(self) -> dict:
        return {
            "id": f"tool-{self.name}",
            "content": (
                f"Tool: {self.name}\n"
                f"Category: {self.category}\n"
                f"Risk tier: {self.tier}\n"
                f"Description: {self.description}\n"
                f"Parameters: {self.parameters}\n"
                f"Required: {self.required_params}"
            ),
            "metadata": {
                "tool_name": self.name,
                "category": self.category,
                "tier": self.tier,
            },
        }

    def to_llm_schema(self) -> dict:
        """Return a dict compatible with Vertex AI FunctionDeclaration."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            },
        }

    def to_vertex_function_declaration(self):
        """Return a Vertex AI FunctionDeclaration object."""
        from vertexai.generative_models import FunctionDeclaration
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            },
        )


@dataclass
class ToolResult:
    """Result from executing a tool."""
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    tool_name: str = ""


class AEApiClient:
    """HTTP client for the AutomationEdge REST API."""

    def __init__(self):
        self.base_url = CONFIG["AE_BASE_URL"].rstrip("/")
        self.api_key = CONFIG["AE_API_KEY"]
        self.timeout = CONFIG.get("AE_TIMEOUT_SECONDS", 30)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
            verify=False,  # on-prem may use self-signed certs
        )

    def get(self, path: str, params: dict = None) -> dict:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict = None) -> dict:
        resp = self._client.post(path, json=payload or {})
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self._client.close()


_ae_client = None


def get_ae_client() -> AEApiClient:
    """Lazy singleton — only creates the HTTP client on first use."""
    global _ae_client
    if _ae_client is None:
        _ae_client = AEApiClient()
    return _ae_client
