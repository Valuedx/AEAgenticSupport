from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.llm_client import VertexAIClient, genai_types


class TestLLMRateLimitRecovery(unittest.TestCase):
    def _build_client(self) -> VertexAIClient:
        client = VertexAIClient.__new__(VertexAIClient)
        client.model_name = "test-model"
        client.default_temp = 0.1
        client.default_max_tokens = 8192
        client.client = SimpleNamespace(models=SimpleNamespace(generate_content=MagicMock()))
        client._record_usage = MagicMock()
        client._extract_text = MagicMock(return_value="ok")
        return client

    def test_chat_with_tools_retries_with_reduced_input_after_rate_limit(self):
        client = self._build_client()
        response = SimpleNamespace(candidates=[], usage_metadata=None)
        client.client.models.generate_content.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            response,
        ]

        messages = [
            genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=f"message-{idx}-" + ("x" * 2500))],
            )
            for idx in range(8)
        ]

        result = client.chat_with_tools(messages=messages, tools=[], system="sys")

        self.assertIs(result, response)
        self.assertEqual(client.client.models.generate_content.call_count, 2)

        first_call = client.client.models.generate_content.call_args_list[0]
        second_call = client.client.models.generate_content.call_args_list[1]

        self.assertEqual(len(first_call.kwargs["contents"]), 8)
        self.assertLessEqual(len(second_call.kwargs["contents"]), 6)
        self.assertLess(
            second_call.kwargs["config"].max_output_tokens,
            first_call.kwargs["config"].max_output_tokens,
        )


if __name__ == "__main__":
    unittest.main()
