"""支持用一句自然语言启动规划的 LLM 版命令行入口。"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.amap_client import AmapClient, AmapError
from cycling_agent.llm_agent import LLMCyclingAgent
from cycling_agent.llm_parser import LLMParsingError, LLMTripRequestParser
from cycling_agent.gpx_io import GpxError, export_plan_gpx, import_gpx
from cycling_agent.models import TripRequest
from cycling_agent.parser_factory import create_parser
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


def complete_request(draft, *, gpx_route_name: str | None = None) -> TripRequest:
    """LLM 没有猜测的字段，由命令行继续向用户询问。"""

    values = draft.model_dump()
    if gpx_route_name:
        gpx_defaults = {
            "start_city": "GPX",
            "start_name": f"{gpx_route_name}轨迹起点",
            "end_city": "GPX",
            "end_name": f"{gpx_route_name}轨迹终点",
        }
        for field_name, default_value in gpx_defaults.items():
            if values[field_name] is None:
                values[field_name] = default_value

    for field_name, prompt in TEXT_FIELD_PROMPTS.items():
        while values[field_name] is None:
            value = input(prompt).strip()
            if value:
                values[field_name] = value

    for field_name, prompt in STANDARD_FIELD_PROMPTS.items():
        if values[field_name] is None:
            values[field_name] = read_positive_number(prompt)

    return draft.to_trip_request(**values)


def print_understanding(
    request: TripRequest,
    provider: str,
    model: str,
    using_gpx: bool = False,
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
    if using_gpx:
        print("接下来严格按 GPX 轨迹计算里程和搜索 POI，不重新规划主路线。")
    else:
        print("接下来由高德工具计算真实道路和 POI，LLM 不参与距离计算。")


def print_agent_trace(result) -> None:
    """展示可审计的决策、工具调用与观察，不展示隐藏思维过程。"""

    print("\n=== Agent 执行轨迹 ===")
    for event in result.trace:
        labels = {
            "decision": "决策",
            "observation": "观察",
            "complete": "完成",
        }
        print(
            f"  [{event.step}] {labels.get(event.event_type, event.event_type)} "
            f"{event.name}：{event.summary}"
        )

    print("\n=== Agent 约束校验 ===")
    for check in result.validation.checks:
        mark = "通过" if check.passed else "未通过"
        print(f"  - [{mark}] {check.name}：{check.detail}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="骑行食宿规划 LLM Agent",
    )
    parser.add_argument(
        "--gpx",
        type=Path,
        help="导入一条 GPX 固定轨迹，跳过高德主路线重新规划",
    )
    parser.add_argument(
        "--output-gpx",
        type=Path,
        help="导出包含午饭、休息点、住宿和晚饭标记的 GPX",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    load_dotenv()
    amap_api_key = os.getenv("AMAP_API_KEY")

    if not amap_api_key:
        print("没有找到 AMAP_API_KEY，请检查 .env 文件")
        return

    imported_gpx = None
    if args.gpx:
        try:
            imported_gpx = import_gpx(args.gpx)
        except GpxError as error:
            print(f"GPX 导入失败：{error}")
            return
        print("=== GPX 固定路线已导入 ===")
        print(f"路线名称：{imported_gpx.name}")
        print(f"轨迹点数：{imported_gpx.point_count}")
        print(f"累计里程：{imported_gpx.route.distance_meters / 1000:.2f} 公里")

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

    request = complete_request(
        draft,
        gpx_route_name=imported_gpx.name if imported_gpx else None,
    )
    print_understanding(
        request,
        provider,
        parser.model,
        using_gpx=imported_gpx is not None,
    )

    try:
        result = agent.plan_request(
            request,
            route=imported_gpx.route if imported_gpx else None,
        )
    except (AmapError, ValueError) as error:
        print(f"地图规划失败：{error}")
        return

    print_trip_plan(result.plan)
    print_agent_trace(result)

    output_path = args.output_gpx
    if imported_gpx is not None and output_path is None:
        output_path = args.gpx.with_name(f"{args.gpx.stem}_planned.gpx")

    if output_path is not None:
        try:
            written_path = export_plan_gpx(
                result.plan,
                output_path,
                route_name=(
                    f"{imported_gpx.name}（含食宿休息点）"
                    if imported_gpx
                    else "骑行食宿规划路线"
                ),
                segments_wgs84=(
                    imported_gpx.segments_wgs84 if imported_gpx else None
                ),
                source_path=args.gpx,
            )
        except GpxError as error:
            print(f"\nGPX 导出失败：{error}")
            return
        print(f"\n已导出带推荐点的 GPX：{written_path.resolve()}")


if __name__ == "__main__":
    main()
