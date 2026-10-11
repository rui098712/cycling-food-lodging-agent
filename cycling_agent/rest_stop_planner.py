"""沿骑行道路规划周期性休息点。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from cycling_agent.amap_client import AmapError
from cycling_agent.models import BicycleRoute, Poi, RestStopPlan
from cycling_agent.route_progress import (
    RouteProjection,
    project_point_to_route,
    road_window_search_points,
    route_road_distance,
)


@dataclass(frozen=True)
class _RestCandidate:
    poi: Poi
    category: str
    projection: RouteProjection


class RestStopPlanner:
    """以实际道路里程为尺度，选择尽量不偏离原路线的休息点。"""

    GAS_STATION_TYPE = "010100"
    STORE_TYPE = "060000"
    STORE_KEYWORDS = "便利店|超市|商店"

    PREFERRED_START_METERS = 15_000
    PREFERRED_END_METERS = 20_000
    EXTENDED_END_METERS = 30_000
    ON_ROUTE_TOLERANCE_METERS = 100
    MAX_ROUTE_OFFSET_METERS = 300

    SEARCH_RADIUS_METERS = 3000
    SEARCH_ANCHOR_GAP_METERS = 5000
    SEARCH_MAX_WORKERS = 4

    def __init__(self, amap):
        self.amap = amap
        self.warnings: tuple[str, ...] = ()

    def plan(self, route: BicycleRoute) -> tuple[RestStopPlan, ...]:
        total_distance = route_road_distance(route.steps)
        # search_origin 决定下一个搜索窗口；last_stop_progress 只记录
        # 真正找到的休息点。两者分开后，搜索失败时既不会留下
        # 未搜索的路段，也不会把“距上一个休息点”算错。
        search_origin = 0.0
        last_stop_progress = 0.0
        sequence = 1
        results = []
        warnings: list[str] = []
        self._warnings = warnings

        while (
            total_distance - last_stop_progress > self.PREFERRED_END_METERS
            and search_origin + self.PREFERRED_START_METERS < total_distance
        ):
            preferred_start = search_origin + self.PREFERRED_START_METERS
            preferred_end = min(
                search_origin + self.PREFERRED_END_METERS,
                total_distance,
            )

            selected = self._select_in_window(
                route,
                preferred_start,
                preferred_end,
            )
            search_mode = "preferred"
            search_end = preferred_end

            if selected is None and preferred_end < total_distance:
                extended_start = preferred_end
                extended_end = min(
                    search_origin + self.EXTENDED_END_METERS,
                    total_distance,
                )
                selected = self._select_in_window(
                    route,
                    extended_start,
                    extended_end,
                )
                search_mode = "extended"
                search_end = extended_end

            if selected is None:
                results.append(
                    RestStopPlan(
                        sequence=sequence,
                        search_start_meters=round(preferred_start),
                        search_end_meters=round(search_end),
                        search_mode="not_found",
                    )
                )
                if search_end >= total_distance:
                    break

                # 上一轮已覆盖 [origin+15, origin+30]。将起点只
                # 前移 15 km，下一轮从 origin+30 开始，不留空档。
                search_origin += self.PREFERRED_START_METERS
                sequence += 1
                continue

            projection = selected.projection
            route_progress = round(projection.route_progress_meters)
            offset = round(projection.distance_from_route_meters)
            if offset > self.ON_ROUTE_TOLERANCE_METERS:
                search_mode = f"{search_mode}_nearby"

            results.append(
                RestStopPlan(
                    sequence=sequence,
                    search_start_meters=round(preferred_start),
                    search_end_meters=round(search_end),
                    search_mode=search_mode,
                    poi=selected.poi,
                    category=selected.category,
                    route_progress_meters=route_progress,
                    distance_from_route_meters=offset,
                    distance_since_previous_meters=round(
                        projection.route_progress_meters - last_stop_progress
                    ),
                )
            )
            search_origin = projection.route_progress_meters
            last_stop_progress = projection.route_progress_meters
            sequence += 1

        self.warnings = tuple(warnings)
        return tuple(results)

    def _select_in_window(
        self,
        route: BicycleRoute,
        window_start: float,
        window_end: float,
    ) -> _RestCandidate | None:
        gas_candidates = self._search_candidates(
            route,
            window_start,
            window_end,
            types=self.GAS_STATION_TYPE,
            keywords=None,
            category="加油站",
        )
        target = (window_start + window_end) / 2
        gas_on_route = self._within_offset(
            gas_candidates,
            self.ON_ROUTE_TOLERANCE_METERS,
        )
        if gas_on_route:
            return self._closest_to_target(gas_on_route, target)

        store_candidates = self._search_candidates(
            route,
            window_start,
            window_end,
            types=self.STORE_TYPE,
            keywords=self.STORE_KEYWORDS,
            category="商店",
        )

        priority_groups = (
            self._within_offset(store_candidates, self.ON_ROUTE_TOLERANCE_METERS),
            self._offset_band(
                gas_candidates,
                self.ON_ROUTE_TOLERANCE_METERS,
                self.MAX_ROUTE_OFFSET_METERS,
            ),
            self._offset_band(
                store_candidates,
                self.ON_ROUTE_TOLERANCE_METERS,
                self.MAX_ROUTE_OFFSET_METERS,
            ),
        )

        for candidates in priority_groups:
            if candidates:
                return self._closest_to_target(candidates, target)

        return None

    @staticmethod
    def _closest_to_target(
        candidates: tuple[_RestCandidate, ...],
        target: float,
    ) -> _RestCandidate:
        return min(
            candidates,
            key=lambda candidate: (
                abs(candidate.projection.route_progress_meters - target),
                candidate.projection.distance_from_route_meters,
            ),
        )

    def _search_candidates(
        self,
        route: BicycleRoute,
        window_start: float,
        window_end: float,
        *,
        types: str,
        keywords: str | None,
        category: str,
    ) -> tuple[_RestCandidate, ...]:
        pois: dict[str, Poi] = {}
        search_points = road_window_search_points(
            route.steps,
            window_start,
            window_end,
            max_road_gap_meters=self.SEARCH_ANCHOR_GAP_METERS,
        )

        def search_one(search_point):
            _, location = search_point
            try:
                found = self.amap.search_nearby(
                    location,
                    types=types,
                    radius=self.SEARCH_RADIUS_METERS,
                    keywords=keywords,
                    sort_rule="distance",
                )
                return found, None
            except AmapError as error:
                return (), f"休息点查询部分失败：{error}"

        worker_count = min(self.SEARCH_MAX_WORKERS, len(search_points))
        if worker_count <= 1:
            search_results = tuple(search_one(point) for point in search_points)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                search_results = tuple(executor.map(search_one, search_points))

        for found, warning in search_results:
            if warning and warning not in self._warnings:
                self._warnings.append(warning)
            for poi in found:
                key = poi.poi_id or f"{poi.name}:{poi.location}"
                pois[key] = poi

        candidates = []
        for poi in pois.values():
            projection = project_point_to_route(route.steps, poi.location)
            if not window_start <= projection.route_progress_meters <= window_end:
                continue
            if projection.distance_from_route_meters > self.MAX_ROUTE_OFFSET_METERS:
                continue
            candidates.append(_RestCandidate(poi, category, projection))

        return tuple(candidates)

    @staticmethod
    def _within_offset(
        candidates: tuple[_RestCandidate, ...],
        maximum: int,
    ) -> tuple[_RestCandidate, ...]:
        return tuple(
            candidate
            for candidate in candidates
            if candidate.projection.distance_from_route_meters <= maximum
        )

    @staticmethod
    def _offset_band(
        candidates: tuple[_RestCandidate, ...],
        minimum: int,
        maximum: int,
    ) -> tuple[_RestCandidate, ...]:
        return tuple(
            candidate
            for candidate in candidates
            if minimum < candidate.projection.distance_from_route_meters <= maximum
        )
