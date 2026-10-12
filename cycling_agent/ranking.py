"""候选地点的可解释排序规则。"""

from cycling_agent.models import Poi, Recommendation


def rank_pois(
    pois: tuple[Poi, ...],
    spending_standard_per_person: float,
    *,
    limit: int = 5,
) -> tuple[Recommendation, ...]:
    """综合人均消费标准、评分和距离排序；午饭可附加聚落优先级。"""

    recommendations = []

    for poi in pois:
        reasons = []

        if poi.rating is None:
            rating_score = 15.0
            reasons.append("高德暂无评分")
        else:
            rating_score = min(max(poi.rating, 0), 5) / 5 * 50
            reasons.append(f"高德评分 {poi.rating:.1f}")

        if poi.cost is None:
            standard_score = 10.0
            reasons.append("价格需到店确认")
        elif poi.cost <= spending_standard_per_person:
            standard_score = 40.0
            reasons.append(
                f"参考人均消费 {poi.cost:.0f} 元，符合人均标准"
            )
        else:
            over_ratio = (
                poi.cost - spending_standard_per_person
            ) / spending_standard_per_person
            standard_score = max(0.0, 40 * (1 - over_ratio))
            reasons.append(
                f"参考人均消费 {poi.cost:.0f} 元，超过人均标准"
            )

        if poi.distance_meters is None:
            distance_score = 5.0
        else:
            distance_score = 10 * max(0, 1 - poi.distance_meters / 3000)
            reasons.append(f"距搜索点约 {poi.distance_meters} 米")

        settlement_score = 0.0
        if poi.lunch_area_kind == "town":
            settlement_score = 12.0
            reasons.append(f"靠近 {poi.township_name} 镇区，午饭优先")
        elif poi.lunch_area_kind == "urban":
            settlement_score = 12.0
            area_name = poi.township_name or poi.district_name or poi.city_name
            reasons.append(f"靠近 {area_name} 市区，午饭优先")

        recommendations.append(
            Recommendation(
                poi=poi,
                score=round(
                    rating_score
                    + standard_score
                    + distance_score
                    + settlement_score,
                    1,
                ),
                reasons=tuple(reasons),
            )
        )

    recommendations.sort(
        key=lambda item: (
            item.score,
            item.poi.rating if item.poi.rating is not None else -1,
        ),
        reverse=True,
    )
    return tuple(recommendations[:limit])
