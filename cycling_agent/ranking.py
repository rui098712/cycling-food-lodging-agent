"""候选地点的可解释排序规则。"""

from cycling_agent.models import Poi, Recommendation


def rank_pois(
    pois: tuple[Poi, ...],
    budget: float,
    *,
    limit: int = 5,
) -> tuple[Recommendation, ...]:
    """综合预算、评分和距离排序；缺失字段不会被伪造。"""

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
            budget_score = 10.0
            reasons.append("价格需到店确认")
        elif poi.cost <= budget:
            budget_score = 40.0
            reasons.append(f"参考消费 {poi.cost:.0f} 元，在预算内")
        else:
            over_ratio = (poi.cost - budget) / budget
            budget_score = max(0.0, 40 * (1 - over_ratio))
            reasons.append(f"参考消费 {poi.cost:.0f} 元，超过预算")

        if poi.distance_meters is None:
            distance_score = 5.0
        else:
            distance_score = 10 * max(0, 1 - poi.distance_meters / 3000)
            reasons.append(f"距搜索点约 {poi.distance_meters} 米")

        recommendations.append(
            Recommendation(
                poi=poi,
                score=round(rating_score + budget_score + distance_score, 1),
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
