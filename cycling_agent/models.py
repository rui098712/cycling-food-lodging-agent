"""项目中使用的数据模型。"""

from dataclasses import dataclass, field


Coordinate = tuple[float, float]


@dataclass(frozen=True)
class GeoPlace:
    formatted_address: str
    location: Coordinate


@dataclass(frozen=True)
class AdministrativeArea:
    """一个路线采样点所属的省、市、县区和乡镇街道。"""

    province_name: str = ""
    city_name: str = ""
    district_name: str = ""
    township_name: str = ""


@dataclass(frozen=True)
class RouteStep:
    """一段可定位的骑行道路或 GPX 轨迹。"""

    distance_meters: int
    polyline: tuple[Coordinate, ...]
    instruction: str = ""
    road_name: str = ""


@dataclass(frozen=True)
class BicycleRoute:
    distance_meters: int
    duration_seconds: int
    steps: tuple[RouteStep, ...]
    source: str = "amap"
    planning_segment_count: int = 1

    @property
    def point_count(self) -> int:
        return sum(len(step.polyline) for step in self.steps)


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
    city_name: str = ""
    district_name: str = ""
    township_name: str = ""
    lunch_area_kind: str = ""


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
    lunch_standard_per_person: float
    dinner_standard_per_person: float
    lodging_standard_per_person: float


@dataclass(frozen=True)
class TripPlan:
    start: GeoPlace
    end: GeoPlace
    route: BicycleRoute
    lunch_search_point_count: int
    lunch_areas: tuple[AdministrativeArea, ...] = field(default_factory=tuple)
    rest_stops: tuple[RestStopPlan, ...] = field(default_factory=tuple)
    lunch_recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    lodging_options: tuple[LodgingOption, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
