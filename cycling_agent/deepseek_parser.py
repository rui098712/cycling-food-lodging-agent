"""Use DeepSeek's OpenAI-compatible Responses API for trip extraction."""

import os
from typing import Any

from openai import OpenAI

from cycling_agent.llm_parser import LLMTripRequestParser


class DeepSeekTripRequestParser(LLMTripRequestParser):
    """Extract trip fields with DeepSeek while keeping map work deterministic."""

    DEFAULT_MODEL = "deepseek-flash"
    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        resolved_api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if client is None and not resolved_api_key:
            raise ValueError("没有找到 DEEPSEEK_API_KEY，请检查 .env 文件")

        deepseek_client = client or OpenAI(
            api_key=resolved_api_key,
            base_url=base_url
            or os.getenv("DEEPSEEK_BASE_URL", self.DEFAULT_BASE_URL),
        )
        super().__init__(
            client=deepseek_client,
            model=model or os.getenv("DEEPSEEK_MODEL", self.DEFAULT_MODEL),
            provider_name="DeepSeek",
            request_options={
                # Parameter extraction does not benefit from a long reasoning trace.
                "max_output_tokens": 1200,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        )
