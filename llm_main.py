"""支持用一句自然语言启动规划的 LLM 版命令行入口。"""

import os

from dotenv import load_dotenv

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.amap_client import AmapClient, AmapError
from cycling_agent.llm_agent import LLMCyclingAgent
from cycling_agent.llm_parser import LLMParsingError, LLMTripRequestParser
from cycling_agent.models import TripRequest
from cycling_agent.ollama_parser import OllamaTripRequestParser
from main import print_trip_plan, read_positive_number


TEXT_FIELD_PROMPTS = {
    "start_city": "请补充起点城市或县：",
    "start_name": "请补充骑行起点：",
    "end_city": "请补充终点城市或县：",
    "end_name": "请补充骑行终点：",
}

STANDARD_FIELD_PROMPTS = {
    "lunch_standard_per_person": "请补充人均午饭餐标（元/人）：",
    "dinner_standard_per_person": "请补充人均晚饭餐标（元/人）：",
    "lodging_standard_per_person": "请补充人均住宿标准（元/人）：",
}


def complete_request(draft) -> TripRequest:
    """LLM 没有猜测的字段，由命令行继续向用户询问。"""

    values = draft.model_dump()
    for field_name, prompt in TEXT_FIELD_PROMPTS.items():
        while values[field_name] is None:
            value = input(prompt).strip()
            if value:
                values[field_name] = value

    for field_name, prompt in STANDARD_FIELD_PROMPTS.items():
        if values[field_name] is None:
            values[field_name] = read_positive_number(prompt)

    return draft.to_trip_request(**values)


def create_parser(provider: str | None = None):
    """根据配置创建本地或云端解析器。"""

    provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).strip().lower()
    if provider == "ollama":
        return provider, OllamaTripRequestParser()
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError(
                "LLM_PROVIDER=openai 时需要在 .env 中配置 OPENAI_API_KEY"
            )
        return provider, LLMTripRequestParser()
    raise ValueError("LLM_PROVIDER 只能是 ollama 或 openai")


def print_understanding(
    request: TripRequest,
    provider: str,
    model: str,
) -> None:
    print("\n=== LLM 理解结果 ===")
    print(f"LLM 后端：{provider}")
    print(f"模型：{model}")
    print(f"起点：{request.start_city} {request.start_name}")
    print(f"终点：{request.end_city} {request.end_name}")
    print(
        "人均午饭/晚饭/住宿标准："
        f"{request.lunch_standard_per_person:g} / "
        f"{request.dinner_standard_per_person:g} / "
        f"{request.lodging_standard_per_person:g} 元/人"
    )
    print("接下来由高德工具计算真实道路和 POI，LLM 不参与距离计算。")


def main():
    load_dotenv()
    amap_api_key = os.getenv("AMAP_API_KEY")

    if not amap_api_key:
        print("没有找到 AMAP_API_KEY，请检查 .env 文件")
        return

    try:
        provider, parser = create_parser()
    except ValueError as error:
        print(f"LLM 配置错误：{error}")
        return
    agent = LLMCyclingAgent(
        parser=parser,
        planner=CyclingPlannerAgent(AmapClient(amap_api_key)),
    )

    print("=== 骑行食宿规划 LLM Agent ===")
    print(
        "示例：我想从广州市广州塔骑到深圳市深圳湾公园，"
        "人均午饭50，人均晚饭80，人均住宿250。"
    )
    user_text = input("请用一句话描述行程：").strip()

    try:
        draft = agent.understand(user_text)
    except (LLMParsingError, ValueError) as error:
        print(f"LLM 理解失败：{error}")
        return

    request = complete_request(draft)
    print_understanding(request, provider, parser.model)

    try:
        result = agent.plan_request(request)
    except AmapError as error:
        print(f"地图规划失败：{error}")
        return

    print_trip_plan(result.plan)


if __name__ == "__main__":
    main()
