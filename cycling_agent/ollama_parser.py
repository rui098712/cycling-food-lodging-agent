"""使用本机 Ollama 模型将自然语言骑行需求转换为结构化数据。"""

import os
from typing import Any

from ollama import Client, ResponseError
from pydantic import ValidationError

from cycling_agent.llm_parser import (
    LLMParsingError,
    TRIP_EXTRACTION_INSTRUCTIONS,
    TripRequestDraft,
)


class OllamaTripRequestParser:
    """用本机 Ollama 的结构化输出提取行程参数。"""

    DEFAULT_MODEL = "qwen3:4b"

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        host: str | None = None,
    ):
        self.client = client or Client(
            host=host or os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            # 本地服务不应经过系统代理，否则可能出现 502。
            trust_env=False,
        )
        self.model = model or os.getenv("OLLAMA_MODEL", self.DEFAULT_MODEL)

    def parse(self, user_text: str) -> TripRequestDraft:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("行程描述不能为空")

        schema = TripRequestDraft.model_json_schema()
        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": TRIP_EXTRACTION_INSTRUCTIONS},
                    {"role": "user", "content": user_text},
                ],
                format=schema,
                options={"temperature": 0},
                think=False,
            )
            content = response.message.content
            if not content:
                raise LLMParsingError("本地模型没有返回内容")
            return TripRequestDraft.model_validate_json(content)
        except LLMParsingError:
            raise
        except ConnectionError as error:
            raise LLMParsingError(
                "无法连接 Ollama，请先在终端运行 ollama serve"
            ) from error
        except ResponseError as error:
            raise LLMParsingError(f"Ollama 请求失败：{error}") from error
        except ValidationError as error:
            raise LLMParsingError(
                f"本地模型输出不符合行程结构：{error}"
            ) from error
