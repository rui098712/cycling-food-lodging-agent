"""Agent 编排层：根据目标选择工具，并把工具结果组织成行程方案。"""

from cycling_agent.amap_client import AmapClient, AmapError
from cycling_agent.models import (
    LodgingOption,
    Poi,
    Recommendation,
    TripPlan,
    TripRequest,
)
from cycling_agent.ranking import rank_pois
from cycling_agent.route_progress import coordinate_distance_meters, lunch_search_points


class CyclingPlannerAgent:
    """第一版工作流 Agent；决策过程明确、可测试，暂不依赖大模型。"""

    RESTAURANT_TYPE = "050000"
    LODGING_TYPE = "100000"
    LUNCH_SEARCH_RADIUS_METERS = 2500
    LUNCH_MAX_ROAD_GAP_METERS = 4000
    DINNER_LODGING_MAX_ROAD_DISTANCE_METERS = 3000
    LODGING_CANDIDATE_LIMIT = 5
    DINNER_CANDIDATE_LIMIT = 6
    MAX_PAIR_ROUTE_CHECKS = 10

    def __init__(self, amap: AmapClient):
        self.amap = amap

    def plan(self, request: TripRequest) -> TripPlan:
        # 1. 理解输入：把地点名称转换成机器可使用的坐标。
        start = self.amap.geocode(request.start_name, request.start_city)
        end = self.amap.geocode(request.end_name, request.end_city)

        # 2. 调用路线工具：获得高德规划的骑行道路及每段道路里程。
        route = self.amap.bicycle_route(start.location, end.location)

        # 3. 按路线长度动态增加搜索中心，连续覆盖 1/2～2/3 区间。
        lunch_candidates: dict[str, Poi] = {}
        search_points = lunch_search_points(
            route.steps,
            max_road_gap_meters=self.LUNCH_MAX_ROAD_GAP_METERS,
        )
        for ratio, location in search_points:
            pois = self.amap.search_nearby(
                location,
                types=self.RESTAURANT_TYPE,
                radius=self.LUNCH_SEARCH_RADIUS_METERS,
                keywords="餐厅|饭店|小吃",
                route_ratio=ratio,
            )
            self._merge_unique(lunch_candidates, pois)

        # 4. 住宿是必要条件，因此先查询和排序终点附近的住宿。
        lodging_candidates = self.amap.search_nearby(
            end.location,
            types=self.LODGING_TYPE,
            radius=5000,
        )
        lodging_candidates = tuple(
            poi for poi in lodging_candidates if self._is_lodging(poi)
        )
        lodging_recommendations = rank_pois(
            lodging_candidates,
            request.lodging_budget,
            limit=self.LODGING_CANDIDATE_LIMIT,
        )

        # 5. 晚饭是可选增强：逐个住宿寻找 3 公里内晚饭，找不到也保留住宿。
        return TripPlan(
            start=start,
            end=end,
            route=route,
            lunch_search_point_count=len(search_points),
            lunch_recommendations=rank_pois(
                tuple(lunch_candidates.values()), request.lunch_budget
            ),
            lodging_options=self._build_lodging_options(
                lodging_recommendations,
                request.dinner_budget,
            ),
        )

    def _build_lodging_options(
        self,
        lodgings: tuple[Recommendation, ...],
        dinner_budget: float,
    ) -> tuple[LodgingOption, ...]:
        """住宿优先；若能验证到 3 公里内晚饭，则附加到住宿方案。"""

        options = []
        route_checks = 0

        for lodging in lodgings:
            if route_checks >= self.MAX_PAIR_ROUTE_CHECKS:
                options.append(LodgingOption(lodging=lodging))
                continue

            dinner_candidates = self.amap.search_nearby(
                lodging.poi.location,
                types=self.RESTAURANT_TYPE,
                radius=self.DINNER_LODGING_MAX_ROAD_DISTANCE_METERS,
                keywords="餐厅|饭店|小吃",
            )
            dinners = rank_pois(
                dinner_candidates,
                dinner_budget,
                limit=self.DINNER_CANDIDATE_LIMIT,
            )

            matched_dinner = None
            matched_distance = None

            for dinner in dinners:
                if route_checks >= self.MAX_PAIR_ROUTE_CHECKS:
                    break

                direct_distance = coordinate_distance_meters(
                    lodging.poi.location,
                    dinner.poi.location,
                )
                if direct_distance > self.DINNER_LODGING_MAX_ROAD_DISTANCE_METERS:
                    continue

                route_checks += 1
                try:
                    road_distance = self.amap.bicycle_distance(
                        lodging.poi.location,
                        dinner.poi.location,
                    )
                except AmapError:
                    continue

                if road_distance <= self.DINNER_LODGING_MAX_ROAD_DISTANCE_METERS:
                    matched_dinner = dinner
                    matched_distance = road_distance
                    break

            options.append(
                LodgingOption(
                    lodging=lodging,
                    dinner=matched_dinner,
                    dinner_road_distance_meters=matched_distance,
                )
            )

        return tuple(options)

    @staticmethod
    def _is_lodging(poi: Poi) -> bool:
        """过滤住宿大类中与实际住宿无关的附属设施。"""

        lodging_words = (
            "酒店",
            "宾馆",
            "旅馆",
            "旅舍",
            "青旅",
            "客栈",
            "民宿",
            "公寓",
        )
        return any(word in poi.name for word in lodging_words)

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
