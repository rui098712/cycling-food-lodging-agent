"""使用 LLM 将用户的自然语言骑行需求转换为结构化数据。"""

import os
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cycling_agent.models import TripRequest


TRIP_EXTRACTION_INSTRUCTIONS = """
你是骑行行程参数提取器。只提取用户明确表达的信息，不调用地图，不猜测路线或地理位置。

规则：
1. 分别提取起点行政区、起点名称、终点行政区、终点名称。
2. 县、自治县或区可以与市名一起保留在 city 字段，不要擅自改成另一个城市。
3. 分别提取人均午饭餐标、人均晚饭餐标和人均住宿标准，单位都是人民币元/人。
4. 信息没有明确出现时返回 null，禁止自行补全消费标准或地名。
5. 你只负责理解输入；真实道路里程、POI 和推荐由后续地图工具计算。
""".strip()


class LLMParsingError(RuntimeError):
    """LLM 请求失败，或没有返回可用的结构化结果。"""


class MissingTripFieldsError(ValueError):
    """自然语言中缺少生成完整行程所必需的字段。"""

    def __init__(self, fields: tuple[str, ...]):
        self.fields = fields
        super().__init__(f"行程信息不完整：{', '.join(fields)}")


class TripRequestDraft(BaseModel):
    """LLM 输出的严格结构；信息不足时对应字段为 None。"""

    model_config = ConfigDict(extra="forbid")

    start_city: str | None = Field(
        description="起点所在的市或县级行政区；用户没说时为 null"
    )
    start_name: str | None = Field(
        description="用户明确给出的骑行起点地点名称；缺失时为 null"
    )
    end_city: str | None = Field(
        description="终点所在的市或县级行政区；用户没说时为 null"
    )
    end_name: str | None = Field(
        description="用户明确给出的骑行终点地点名称；缺失时为 null"
    )
    lunch_standard_per_person: float | None = Field(
        description="人均午饭餐标，单位为人民币元/人；缺失时为 null",
        gt=0,
    )
    dinner_standard_per_person: float | None = Field(
        description="人均晚饭餐标，单位为人民币元/人；缺失时为 null",
        gt=0,
    )
    lodging_standard_per_person: float | None = Field(
        description="人均住宿消费标准，单位为人民币元/人；缺失时为 null",
        gt=0,
    )

    @field_validator("start_city", "start_name", "end_city", "end_name")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "start_city",
                "start_name",
                "end_city",
                "end_name",
                "lunch_standard_per_person",
                "dinner_standard_per_person",
                "lodging_standard_per_person",
            )
            if getattr(self, name) is None
        )

    def to_trip_request(self, **overrides: Any) -> TripRequest:
        """用可选的补充值生成现有地图规划器使用的 TripRequest。"""

        values = self.model_dump()
        values.update(
            {
                key: value
                for key, value in overrides.items()
                if key in values and value is not None
            }
        )
        completed = type(self).model_validate(values)
        missing = completed.missing_fields()
        if missing:
            raise MissingTripFieldsError(missing)

        return TripRequest(**completed.model_dump())


class LLMTripRequestParser:
    """用 OpenAI Structured Outputs 提取行程参数。"""

    DEFAULT_MODEL = "gpt-6-astra"
    SYSTEM_INSTRUCTIONS = TRIP_EXTRACTION_INSTRUCTIONS

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        provider_name: str = "OpenAI",
        request_options: dict[str, Any] | None = None,
    ):
        self.client = client or OpenAI()
        self.model = model or os.getenv("OPENAI_MODEL", self.DEFAULT_MODEL)
        self.provider_name = provider_name
        self.request_options = request_options or {}

    def parse(self, user_text: str) -> TripRequestDraft:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("行程描述不能为空")

        try:
            response = self.client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": self.SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": user_text},
                ],
                text_format=TripRequestDraft,
                **self.request_options,
            )
        except OpenAIError as error:
            status_code = getattr(error, "status_code", None)
            status_hints = {
                401: "API Key 无效或已失效",
                402: "账户余额不足",
                429: "请求过于频繁，请稍后重试",
            }
            hint = status_hints.get(status_code)
            detail = hint or str(error)
            raise LLMParsingError(
                f"{self.provider_name} 请求失败：{detail}"
            ) from error
        except ValidationError as error:
            raise LLMParsingError(
                f"{self.provider_name} 结构化解析失败：{error}"
            ) from error

        parsed = response.output_parsed
        if parsed is None:
            raise LLMParsingError(
                f"{self.provider_name} 没有返回可用的结构化行程参数"
            )
        return parsed
