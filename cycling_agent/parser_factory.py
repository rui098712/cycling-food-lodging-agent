"""Create the configured natural-language parser for every entry point."""

import os

from cycling_agent.deepseek_parser import DeepSeekTripRequestParser
from cycling_agent.llm_parser import LLMTripRequestParser
from cycling_agent.ollama_parser import OllamaTripRequestParser


def create_parser(provider: str | None = None):
    """Return ``(provider_name, parser)`` from environment configuration."""

    provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).strip().lower()
    if provider == "ollama":
        return provider, OllamaTripRequestParser()
    if provider == "deepseek":
        return provider, DeepSeekTripRequestParser()
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError(
                "LLM_PROVIDER=openai 时需要在 .env 中配置 OPENAI_API_KEY"
            )
        return provider, LLMTripRequestParser()
    raise ValueError("LLM_PROVIDER 只能是 ollama、deepseek 或 openai")
