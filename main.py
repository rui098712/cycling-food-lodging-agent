"""骑行食宿规划 Agent 的命令行入口。"""

import os

from dotenv import load_dotenv

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.amap_client import AmapClient, AmapError
from cycling_agent.models import TripRequest


def read_positive_number(prompt: str) -> float:
    """读取一个大于 0 的人均消费标准。"""

    while True:
        text = input(prompt).strip()
        try:
            value = float(text)
        except ValueError:
            print("请输入数字，例如 40 或 150.5")
            continue

        if value > 0:
            return value

        print("人均消费标准必须大于 0")


def print_recommendations(title, recommendations, price_unit="元/人"):
    print(f"\n{title}")

    if not recommendations:
        print("  暂无符合条件的结果")
        return

    for index, recommendation in enumerate(recommendations, start=1):
        poi = recommendation.poi
        cost_text = f"{poi.cost:.0f} {price_unit}" if poi.cost is not None else "价格暂无"
        rating_text = f"评分 {poi.rating:.1f}" if poi.rating is not None else "评分暂无"
        distance_text = (
            f"距搜索点 {poi.distance_meters} 米"
            if poi.distance_meters is not None
            else "距离暂无"
        )

        print(f"  {index}. {poi.name}（推荐分 {recommendation.score:.1f}）")
        print(f"     {cost_text}，{rating_text}，{distance_text}")
        print(f"     地址：{poi.address or '暂无'}")
        print(f"     理由：{'；'.join(recommendation.reasons)}")


def print_lodging_options(options):
    print("\n=== 住宿优先推荐 ===")

    if not options:
        print("  没有找到终点附近的合适住宿")
        return

    for index, option in enumerate(options, start=1):
        lodging = option.lodging.poi
        lodging_cost = (
            f"高德参考人均消费 {lodging.cost:.0f} 元/人"
            if lodging.cost is not None
            else "人均住宿价格暂无"
        )

        print(f"  {index}. {lodging.name}（推荐分 {option.lodging.score:.1f}）")
        print(f"     住宿：{lodging_cost}，地址：{lodging.address or '暂无'}")

        if option.dinner is None:
            print("     晚饭：3 公里实际骑行道路范围内暂无已验证结果")
            continue

        dinner = option.dinner.poi
        dinner_cost = f"{dinner.cost:.0f} 元/人" if dinner.cost is not None else "价格暂无"
        print(f"     晚饭：{dinner.name}（{dinner_cost}）")
        print(
            "     住宿到晚饭的实际骑行道路距离："
            f"{option.dinner_road_distance_meters / 1000:.2f} 公里"
        )


def print_rest_stops(rest_stops):
    print("\n=== 沿途休息点 ===")

    if not rest_stops:
        print("  路程不足 15 公里，无需安排周期性休息点")
        return

    mode_names = {
        "preferred": "15～20 公里优选",
        "preferred_nearby": "15～20 公里、路线周边 300 米内",
        "extended": "延长至 20～30 公里",
        "extended_nearby": "延长至 20～30 公里、路线周边 300 米内",
    }

    for stop in rest_stops:
        if stop.poi is None:
            print(
                f"  {stop.sequence}. 路线 {stop.search_start_meters / 1000:.1f}～"
                f"{stop.search_end_meters / 1000:.1f} 公里内未找到加油站或商店"
            )
            continue

        print(
            f"  {stop.sequence}. {stop.poi.name}（{stop.category}，"
            f"{mode_names.get(stop.search_mode, stop.search_mode)}）"
        )
        print(
            f"     距上一个起算点 {stop.distance_since_previous_meters / 1000:.2f} 公里，"
            f"全程进度 {stop.route_progress_meters / 1000:.2f} 公里"
        )
        print(
            f"     距原骑行路线约 {stop.distance_from_route_meters} 米，"
            f"地址：{stop.poi.address or '暂无'}"
        )


def print_trip_plan(plan):
    """统一输出行程，供传统入口和 LLM 入口共用。"""

    print("\n=== 路线概况 ===")
    print(f"起点：{plan.start.formatted_address}")
    print(f"终点：{plan.end.formatted_address}")
    print(f"骑行道路里程：{plan.route.distance_meters / 1000:.2f} 公里")
    print(f"预计时间：{plan.route.duration_seconds / 3600:.2f} 小时")
    print(
        "午饭搜索范围：骑行道路里程的 50%～66.7%，"
        f"动态生成 {plan.lunch_search_point_count} 个搜索点"
    )

    print_recommendations("=== 午饭推荐 ===", plan.lunch_recommendations)
    print_rest_stops(plan.rest_stops)
    print_lodging_options(plan.lodging_options)

    if plan.warnings:
        print("\n=== 规划警告 ===")
        for warning in plan.warnings:
            print(f"  - {warning}")
        print("  其他已成功返回的结果仍然保留。")


def main():
    load_dotenv()
    api_key = os.getenv("AMAP_API_KEY")

    if not api_key:
        raise RuntimeError("没有找到 AMAP_API_KEY，请检查 .env 文件")

    print("=== 骑行食宿规划 Agent ===")
    request = TripRequest(
        start_city=input("请输入起点城市：").strip(),
        start_name=input("请输入骑行起点：").strip(),
        end_city=input("请输入终点城市：").strip(),
        end_name=input("请输入骑行终点：").strip(),
        lunch_standard_per_person=read_positive_number(
            "请输入人均午饭餐标（元/人）："
        ),
        dinner_standard_per_person=read_positive_number(
            "请输入人均晚饭餐标（元/人）："
        ),
        lodging_standard_per_person=read_positive_number(
            "请输入人均住宿标准（元/人）："
        ),
    )

    if not all(
        [request.start_city, request.start_name, request.end_city, request.end_name]
    ):
        print("城市、起点和终点不能为空")
        return

    agent = CyclingPlannerAgent(AmapClient(api_key))

    try:
        plan = agent.plan(request)
    except AmapError as error:
        print(f"规划失败：{error}")
        return

    print_trip_plan(plan)


if __name__ == "__main__":
    main()
