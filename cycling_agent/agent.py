"""Agent 编排层：根据目标选择工具，并把工具结果组织成行程方案。"""

from cycling_agent.amap_client import AmapClient
from cycling_agent.models import Poi, TripPlan, TripRequest
from cycling_agent.ranking import rank_pois
from cycling_agent.route_progress import lunch_search_points


class CyclingPlannerAgent:
    """第一版工作流 Agent；决策过程明确、可测试，暂不依赖大模型。"""

    RESTAURANT_TYPE = "050000"
    LODGING_TYPE = "100000"

    def __init__(self, amap: AmapClient):
        self.amap = amap

    def plan(self, request: TripRequest) -> TripPlan:
        # 1. 理解输入：把地点名称转换成机器可使用的坐标。
        start = self.amap.geocode(request.start_name, request.start_city)
        end = self.amap.geocode(request.end_name, request.end_city)

        # 2. 调用路线工具：获得高德规划的骑行道路及每段道路里程。
        route = self.amap.bicycle_route(start.location, end.location)

        # 3. 根据实际道路累计里程，在 1/2～2/3 处选择搜索中心。
        lunch_candidates: dict[str, Poi] = {}
        for ratio, location in lunch_search_points(route.steps):
            pois = self.amap.search_nearby(
                location,
                types=self.RESTAURANT_TYPE,
                radius=2500,
                route_ratio=ratio,
            )
            self._merge_unique(lunch_candidates, pois)

        # 4. 在终点附近分别查询晚饭与住宿，它们使用不同预算排序。
        dinner_candidates = self.amap.search_nearby(
            end.location,
            types=self.RESTAURANT_TYPE,
            radius=3000,
        )
        lodging_candidates = self.amap.search_nearby(
            end.location,
            types=self.LODGING_TYPE,
            radius=5000,
        )

        # 5. 形成决策：评分、价格、距离都有明确权重和推荐理由。
        return TripPlan(
            start=start,
            end=end,
            route=route,
            lunch_recommendations=rank_pois(
                tuple(lunch_candidates.values()), request.lunch_budget
            ),
            dinner_recommendations=rank_pois(
                dinner_candidates, request.dinner_budget
            ),
            lodging_recommendations=rank_pois(
                lodging_candidates, request.lodging_budget
            ),
        )

    @staticmethod
    def _merge_unique(target: dict[str, Poi], new_pois: tuple[Poi, ...]) -> None:
        """同一家店可能被多个搜索点找到，只保留离路线搜索点最近的一次。"""

        for poi in new_pois:
            key = poi.poi_id or f"{poi.name}:{poi.location}"
            existing = target.get(key)
            if existing is None:
                target[key] = poi
                continue

            old_distance = existing.distance_meters or 10**9
            new_distance = poi.distance_meters or 10**9
            if new_distance < old_distance:
                target[key] = poi
