"""高德 Web 服务 API 客户端：Agent 可以调用的地图工具。"""

from typing import Any

import requests

from cycling_agent.models import BicycleRoute, Coordinate, GeoPlace, Poi, RouteStep


class AmapError(RuntimeError):
    """高德接口调用失败。"""


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


class AmapClient:
    """封装 HTTP、鉴权和高德响应解析，避免业务代码直接处理 JSON。"""

    def __init__(self, api_key: str, timeout_seconds: int = 10):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {"key": self.api_key, **params}

        try:
            response = self.session.get(
                url,
                params=request_params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            # 不输出完整请求 URL，避免把 API Key 写进日志。
            raise AmapError("网络请求失败或返回内容不是合法 JSON") from error

        if data.get("status") != "1":
            info = data.get("info", "未知错误")
            infocode = data.get("infocode", "未知错误码")
            raise AmapError(f"高德 API 返回失败：{info}（{infocode}）")

        return data

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

    def bicycle_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
    ) -> BicycleRoute:
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
    ) -> tuple[Poi, ...]:
        params = {
            "location": self.format_coordinate(location),
            "types": types,
            "radius": radius,
            "sortrule": "weight",
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
                )
            )

        return tuple(results)

    @staticmethod
    def format_coordinate(location: Coordinate) -> str:
        longitude, latitude = location
        return f"{longitude:.6f},{latitude:.6f}"
