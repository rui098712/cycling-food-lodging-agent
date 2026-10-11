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
class LodgingOption:
    lodging: Recommendation
    dinner: Recommendation | None = None
    dinner_road_distance_meters: int | None = None


@dataclass(frozen=True)
class RestStopPlan:
    sequence: int
    search_start_meters: int
    search_end_meters: int
    search_mode: str
    poi: Poi | None = None
    category: str | None = None
    route_progress_meters: int | None = None
    distance_from_route_meters: int | None = None
    distance_since_previous_meters: int | None = None


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
    lunch_search_point_count: int
    rest_stops: tuple[RestStopPlan, ...] = field(default_factory=tuple)
    lunch_recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    lodging_options: tuple[LodgingOption, ...] = field(default_factory=tuple)
