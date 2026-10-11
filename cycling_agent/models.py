"""项目中使用的数据模型。"""

from dataclasses import dataclass, field


Coordinate = tuple[float, float]


@dataclass(frozen=True)
class GeoPlace:
    formatted_address: str
    location: Coordinate


@dataclass(frozen=True)
class RouteStep:
    """高德返回的一段实际骑行道路。"""

    distance_meters: int
    polyline: tuple[Coordinate, ...]
    instruction: str = ""
    road_name: str = ""


@dataclass(frozen=True)
class BicycleRoute:
    distance_meters: int
    duration_seconds: int
    steps: tuple[RouteStep, ...]


@dataclass(frozen=True)
class Poi:
    poi_id: str
    name: str
    address: str
    location: Coordinate
    type_name: str = ""
    rating: float | None = None
    cost: float | None = None
    distance_meters: int | None = None
    route_ratio: float | None = None


@dataclass(frozen=True)
class Recommendation:
    poi: Poi
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TripRequest:
    start_city: str
    start_name: str
    end_city: str
    end_name: str
    lunch_budget: float
    dinner_budget: float
    lodging_budget: float


@dataclass(frozen=True)
class TripPlan:
    start: GeoPlace
    end: GeoPlace
    route: BicycleRoute
    lunch_recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    dinner_recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    lodging_recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
