"""支持用一句自然语言启动规划的 LLM 版命令行入口。"""

import os

from dotenv import load_dotenv

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.amap_client import AmapClient, AmapError
from cycling_agent.llm_agent import LLMCyclingAgent
from cycling_agent.llm_parser import LLMParsingError, LLMTripRequestParser
from cycling_agent.models import TripRequest
from main import print_trip_plan, read_positive_number


TEXT_FIELD_PROMPTS = {
    "start_city": "请补充起点城市或县：",
    "start_name": "请补充骑行起点：",
    "end_city": "请补充终点城市或县：",
    "end_name": "请补充骑行终点：",
}

BUDGET_FIELD_PROMPTS = {
    "lunch_budget": "请补充午饭餐标（元/人）：",
    "dinner_budget": "请补充晚饭餐标（元/人）：",
    "lodging_budget": "请补充住宿预算（元/晚）：",
}


def complete_request(draft) -> TripRequest:
    """LLM 没有猜测的字段，由命令行继续向用户询问。"""

    values = draft.model_dump()
    for field_name, prompt in TEXT_FIELD_PROMPTS.items():
        while values[field_name] is None:
            value = input(prompt).strip()
            if value:
                values[field_name] = value

    for field_name, prompt in BUDGET_FIELD_PROMPTS.items():
        if values[field_name] is None:
            values[field_name] = read_positive_number(prompt)

    return draft.to_trip_request(**values)


def print_understanding(request: TripRequest, model: str) -> None:
    print("\n=== LLM 理解结果 ===")
    print(f"模型：{model}")
    print(f"起点：{request.start_city} {request.start_name}")
    print(f"终点：{request.end_city} {request.end_name}")
    print(
        f"午饭/晚饭/住宿预算：{request.lunch_budget:g} / "
        f"{request.dinner_budget:g} / {request.lodging_budget:g} 元"
    )
    print("接下来由高德工具计算真实道路和 POI，LLM 不参与距离计算。")


def main():
    load_dotenv()
    amap_api_key = os.getenv("AMAP_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not amap_api_key:
        print("没有找到 AMAP_API_KEY，请检查 .env 文件")
        return
    if not openai_api_key:
        print(
            "没有找到 OPENAI_API_KEY，请先在 .env 中配置后再运行 LLM 版本"
        )
        return

    parser = LLMTripRequestParser()
    agent = LLMCyclingAgent(
        parser=parser,
        planner=CyclingPlannerAgent(AmapClient(amap_api_key)),
    )

    print("=== 骑行食宿规划 LLM Agent ===")
    print("示例：我想从广州市广州塔骑到深圳市深圳湾公园，午饭50，晚饭80，住宿250。")
    user_text = input("请用一句话描述行程：").strip()

    try:
        draft = agent.understand(user_text)
    except (LLMParsingError, ValueError) as error:
        print(f"LLM 理解失败：{error}")
        return

    request = complete_request(draft)
    print_understanding(request, parser.model)

    try:
        result = agent.plan_request(request)
    except AmapError as error:
        print(f"地图规划失败：{error}")
        return

    print_trip_plan(result.plan)


if __name__ == "__main__":
    main()
