"""高德 Web 服务 API 客户端：Agent 可以调用的地图工具。"""

import math
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from cycling_agent.models import (
    AdministrativeArea,
    BicycleRoute,
    Coordinate,
    GeoPlace,
    Poi,
    RouteStep,
)
from cycling_agent.route_progress import coordinate_distance_meters


class AmapError(RuntimeError):
    """高德接口调用失败。"""

    def __init__(self, message: str, *, infocode: str | None = None):
        super().__init__(message)
        self.infocode = infocode


def _parse_coordinate(value: str) -> Coordinate:
    longitude_text, latitude_text = value.split(",")
    return float(longitude_text), float(latitude_text)


def _parse_polyline(value: str) -> tuple[Coordinate, ...]:
    if not value:
        return ()
    return tuple(_parse_coordinate(item) for item in value.split(";") if item)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", []) else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "", []) else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str:
    """高德的空字段可能是空字符串或空数组，统一成空字符串。"""

    return value.strip() if isinstance(value, str) else ""


class AmapClient:
    """封装 HTTP、鉴权和高德响应解析，避免业务代码直接处理 JSON。"""

    QPS_LIMIT_INFOCODES = {"10020", "10021", "10022", "10023", "10029"}
    # 骑行路线过长时，高德可能只返回 30007。道路里程通常长于
    # 直线距离，因此在直线距离达到 450 km 前主动分段，每段目标
    # 约 280 km，然后合并高德返回的真实道路折线。
    LONG_ROUTE_SPLIT_THRESHOLD_METERS = 450_000
    LONG_ROUTE_SEGMENT_TARGET_METERS = 280_000

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 15,
        max_retries: int = 2,
        max_requests_per_second: float = 2.5,
    ):
        if max_requests_per_second <= 0:
            raise ValueError("max_requests_per_second 必须大于 0")

        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.request_interval_seconds = 1 / max_requests_per_second
        self._thread_local = threading.local()
        self._cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_request_time = 0.0

    def _create_session(self) -> requests.Session:
        """为当前线程创建带连接池和自动重试的 Session。"""

        retry = Retry(
            total=self.max_retries,
            connect=self.max_retries,
            read=self.max_retries,
            status=self.max_retries,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=8,
            pool_maxsize=8,
        )
        session = requests.Session()
        session.mount("https://", adapter)
        return session

    @property
    def session(self) -> requests.Session:
        """每个并发线程使用自己的 Session，避免共享状态互相干扰。"""

        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = self._create_session()
            self._thread_local.session = session
        return session

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        cache_key = (url, *sorted(params.items()))
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        request_params = {"key": self.api_key, **params}
        endpoint = url.rsplit("/", 1)[-1]

        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self.session.get(
                    url,
                    params=request_params,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
            except requests.RequestException as error:
                # 不输出完整请求 URL，避免把 API Key 写进日志。
                raise AmapError(
                    f"高德 {endpoint} 请求失败：{type(error).__name__}"
                ) from error

            try:
                data = response.json()
            except ValueError as error:
                if attempt < self.max_retries:
                    time.sleep(0.8 * (2**attempt))
                    continue
                raise AmapError(f"高德 {endpoint} 返回了非法 JSON") from error

            if data.get("status") == "1":
                with self._cache_lock:
                    self._cache[cache_key] = data
                return data

            info = data.get("info", "未知错误")
            infocode = str(data.get("infocode", "未知错误码"))
            # 1002x 通常是限流，300xx 表示地图引擎本次没有正常返回结果。
            # 两者都有可能是暂时性故障，因此只做有限次数的退避重试。
            is_retryable = (
                infocode in self.QPS_LIMIT_INFOCODES
                or infocode.startswith("3")
            )
            if is_retryable and attempt < self.max_retries:
                time.sleep(0.8 * (2**attempt))
                continue

            raise AmapError(
                f"高德 {endpoint} API 返回失败：{info}（{infocode}）",
                infocode=infocode,
            )

        raise AmapError(f"高德 {endpoint} 请求重试后仍失败")

    def _wait_for_request_slot(self) -> None:
        """统一限制并发线程的请求启动速率，避免触发高德 QPS 上限。"""

        with self._rate_lock:
            now = time.monotonic()
            scheduled_time = max(now, self._next_request_time)
            self._next_request_time = scheduled_time + self.request_interval_seconds

        wait_seconds = scheduled_time - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def geocode(self, address: str, city: str) -> GeoPlace:
        data = self._get(
            "https://restapi.amap.com/v3/geocode/geo",
            {"address": address, "city": city},
        )
        geocodes = data.get("geocodes", [])
        if not geocodes:
            raise AmapError(f"没有找到地点：{city} {address}")

        result = geocodes[0]
        return GeoPlace(
            formatted_address=result["formatted_address"],
            location=_parse_coordinate(result["location"]),
        )

    def reverse_geocode(self, location: Coordinate) -> AdministrativeArea:
        """把路线坐标解析为市、县区和乡镇/街道名称。"""

        data = self._get(
            "https://restapi.amap.com/v3/geocode/regeo",
            {
                "location": self.format_coordinate(location),
                "extensions": "base",
            },
        )
        component = data.get("regeocode", {}).get("addressComponent", {})
        if not isinstance(component, dict):
            raise AmapError("逆地理编码没有返回行政区信息")

        province_name = _optional_text(component.get("province"))
        city_name = _optional_text(component.get("city"))
        # 直辖市的 city 字段可能为空，用省级字段补为市名。
        if not city_name and province_name.endswith("市"):
            city_name = province_name

        return AdministrativeArea(
            province_name=province_name,
            city_name=city_name,
            district_name=_optional_text(component.get("district")),
            township_name=_optional_text(component.get("township")),
        )

    def bicycle_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
    ) -> BicycleRoute:
        direct_distance = coordinate_distance_meters(origin, destination)
        if direct_distance > self.LONG_ROUTE_SPLIT_THRESHOLD_METERS:
            return self._segmented_bicycle_route(
                origin,
                destination,
                direct_distance=direct_distance,
            )

        try:
            return self._request_bicycle_route(origin, destination)
        except AmapError as error:
            # 某些道路绕行很长，直线距离未达预分段阈值也可能
            # 超出单次算路能力。仅对较长且明确返回 30007 的路线兜底分段。
            if error.infocode == "30007" and direct_distance > 300_000:
                return self._segmented_bicycle_route(
                    origin,
                    destination,
                    direct_distance=direct_distance,
                )
            raise

    def _request_bicycle_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
    ) -> BicycleRoute:
        """请求一段高德骑行路线。"""

        data = self._get(
            "https://restapi.amap.com/v5/direction/bicycling",
            {
                "origin": self.format_coordinate(origin),
                "destination": self.format_coordinate(destination),
                "show_fields": "polyline",
            },
        )
        paths = data.get("route", {}).get("paths", [])
        if not paths:
            raise AmapError("没有找到可用的骑行路线")

        path = paths[0]
        steps = tuple(
            RouteStep(
                distance_meters=int(step.get("step_distance", 0)),
                polyline=_parse_polyline(step.get("polyline", "")),
                instruction=step.get("instruction", ""),
                road_name=step.get("road_name", ""),
            )
            for step in path.get("steps", [])
        )

        return BicycleRoute(
            distance_meters=int(path["distance"]),
            duration_seconds=int(path["duration"]),
            steps=steps,
        )

    def _segmented_bicycle_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        direct_distance: float,
    ) -> BicycleRoute:
        """沿起终点连线生成分段锚点，逐段请求并合并真实道路。"""

        segment_count = max(
            2,
            math.ceil(
                direct_distance / self.LONG_ROUTE_SEGMENT_TARGET_METERS
            ),
        )
        longitude1, latitude1 = origin
        longitude2, latitude2 = destination
        anchors = tuple(
            (
                longitude1 + (longitude2 - longitude1) * index / segment_count,
                latitude1 + (latitude2 - latitude1) * index / segment_count,
            )
            for index in range(segment_count + 1)
        )

        routes = tuple(
            self._request_bicycle_route(start, end)
            for start, end in zip(anchors, anchors[1:])
        )
        return BicycleRoute(
            distance_meters=sum(route.distance_meters for route in routes),
            duration_seconds=sum(route.duration_seconds for route in routes),
            steps=tuple(step for route in routes for step in route.steps),
            source="amap",
            planning_segment_count=len(routes),
        )

    def bicycle_distance(
        self,
        origin: Coordinate,
        destination: Coordinate,
    ) -> int:
        """只查询两点间的骑行道路里程，用于验证组合约束。"""

        data = self._get(
            "https://restapi.amap.com/v5/direction/bicycling",
            {
                "origin": self.format_coordinate(origin),
                "destination": self.format_coordinate(destination),
            },
        )
        paths = data.get("route", {}).get("paths", [])
        if not paths:
            raise AmapError("两地点之间没有可用的骑行路线")

        return int(paths[0]["distance"])

    def search_nearby(
        self,
        location: Coordinate,
        *,
        types: str,
        radius: int,
        keywords: str | None = None,
        route_ratio: float | None = None,
        page_size: int = 20,
        sort_rule: str = "weight",
    ) -> tuple[Poi, ...]:
        params = {
            "location": self.format_coordinate(location),
            "types": types,
            "radius": radius,
            "sortrule": sort_rule,
            "show_fields": "business",
            "page_size": min(page_size, 25),
        }
        if keywords:
            params["keywords"] = keywords

        data = self._get(
            "https://restapi.amap.com/v5/place/around",
            params,
        )

        results = []
        for item in data.get("pois", []):
            business = item.get("business", {})
            if not isinstance(business, dict):
                business = {}

            location_text = item.get("location")
            if not location_text:
                continue

            results.append(
                Poi(
                    poi_id=item.get("id", ""),
                    name=item.get("name", "未命名地点"),
                    address=item.get("address", "") or "",
                    location=_parse_coordinate(location_text),
                    type_name=item.get("type", "") or "",
                    rating=_optional_float(business.get("rating")),
                    cost=_optional_float(business.get("cost")),
                    distance_meters=_optional_int(item.get("distance")),
                    route_ratio=route_ratio,
                    city_name=_optional_text(item.get("cityname")),
                    district_name=_optional_text(item.get("adname")),
                )
            )

        return tuple(results)

    @staticmethod
    def format_coordinate(location: Coordinate) -> str:
        longitude, latitude = location
        return f"{longitude:.6f},{latitude:.6f}"
