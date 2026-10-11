"""GPX 固定路线的导入和带推荐点路线的导出。"""

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from cycling_agent.coordinate_systems import gcj02_to_wgs84, wgs84_to_gcj02
from cycling_agent.models import (
    BicycleRoute,
    Coordinate,
    Poi,
    RouteStep,
    TripPlan,
)
from cycling_agent.route_progress import coordinate_distance_meters


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
MAX_GPX_FILE_BYTES = 20 * 1024 * 1024
MAX_GPX_POINTS = 200_000


class GpxError(ValueError):
    """GPX 文件无法安全解析或没有可用轨迹。"""


@dataclass(frozen=True)
class ImportedGpxRoute:
    name: str
    segments_wgs84: tuple[tuple[Coordinate, ...], ...]
    route: BicycleRoute

    @property
    def point_count(self) -> int:
        return sum(len(segment) for segment in self.segments_wgs84)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> tuple[ET.Element, ...]:
    return tuple(child for child in element if _local_name(child.tag) == name)


def _first_text(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) == name and child.text:
            text = child.text.strip()
            if text:
                return text
    return None


def _read_points(elements: tuple[ET.Element, ...]) -> tuple[Coordinate, ...]:
    points = []
    for element in elements:
        try:
            latitude = float(element.attrib["lat"])
            longitude = float(element.attrib["lon"])
        except (KeyError, TypeError, ValueError) as error:
            raise GpxError("GPX 轨迹点缺少合法的 lat/lon") from error

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise GpxError("GPX 轨迹点超出合法经纬度范围")
        points.append((longitude, latitude))
    return tuple(points)


def _extract_segments(root: ET.Element) -> tuple[tuple[Coordinate, ...], ...]:
    segments = []
    for track in (item for item in root.iter() if _local_name(item.tag) == "trk"):
        for segment in _children(track, "trkseg"):
            points = _read_points(_children(segment, "trkpt"))
            if len(points) >= 2:
                segments.append(points)

    if segments:
        return tuple(segments)

    # 部分软件导出的是 <rte>/<rtept>，也作为固定路线接受。
    for route in (item for item in root.iter() if _local_name(item.tag) == "rte"):
        points = _read_points(_children(route, "rtept"))
        if len(points) >= 2:
            segments.append(points)
    return tuple(segments)


def _segment_distance(points: tuple[Coordinate, ...]) -> int:
    return round(
        sum(
            coordinate_distance_meters(points[index - 1], points[index])
            for index in range(1, len(points))
        )
    )


def import_gpx(path: str | Path) -> ImportedGpxRoute:
    """读取 WGS-84 GPX，并生成供高德 POI 搜索使用的 GCJ-02 路线。"""

    input_path = Path(path).expanduser()
    if not input_path.is_file():
        raise GpxError(f"找不到 GPX 文件：{input_path}")
    if input_path.stat().st_size > MAX_GPX_FILE_BYTES:
        raise GpxError("GPX 文件超过 20 MB 限制")

    try:
        root = ET.parse(input_path).getroot()
    except (ET.ParseError, OSError) as error:
        raise GpxError(f"GPX 文件解析失败：{error}") from error

    if _local_name(root.tag) != "gpx":
        raise GpxError("文件根节点不是 GPX")

    segments_wgs84 = _extract_segments(root)
    point_count = sum(len(segment) for segment in segments_wgs84)
    if point_count < 2:
        raise GpxError("GPX 中没有至少两个可用的轨迹点")
    if point_count > MAX_GPX_POINTS:
        raise GpxError("GPX 轨迹点超过 200000 个限制")

    steps = tuple(
        RouteStep(
            distance_meters=_segment_distance(segment),
            polyline=tuple(wgs84_to_gcj02(point) for point in segment),
            instruction="按 GPX 固定轨迹骑行",
            road_name="GPX 轨迹",
        )
        for segment in segments_wgs84
    )
    total_distance = sum(step.distance_meters for step in steps)
    if total_distance <= 0:
        raise GpxError("GPX 轨迹总里程为 0，无法用于规划")

    name = _first_text(root, "name") or input_path.stem
    return ImportedGpxRoute(
        name=name,
        segments_wgs84=segments_wgs84,
        route=BicycleRoute(
            distance_meters=total_distance,
            duration_seconds=0,
            steps=steps,
            source="gpx",
        ),
    )


def _add_text(parent: ET.Element, tag: str, text: str | None) -> None:
    if text:
        ET.SubElement(parent, tag).text = text


def _format_cost(poi: Poi) -> str:
    return f"参考消费 {poi.cost:.0f} 元" if poi.cost is not None else "价格暂无"


def _add_waypoint(
    root: ET.Element,
    poi: Poi,
    *,
    name_prefix: str,
    waypoint_type: str,
    description: str,
) -> None:
    longitude, latitude = gcj02_to_wgs84(poi.location)
    waypoint = ET.SubElement(
        root,
        "wpt",
        {"lat": f"{latitude:.8f}", "lon": f"{longitude:.8f}"},
    )
    _add_text(waypoint, "name", f"{name_prefix}：{poi.name}")
    _add_text(waypoint, "desc", description)
    _add_text(waypoint, "type", waypoint_type)


def _route_segments_as_wgs84(
    route: BicycleRoute,
) -> tuple[tuple[Coordinate, ...], ...]:
    return tuple(
        tuple(gcj02_to_wgs84(point) for point in step.polyline)
        for step in route.steps
        if len(step.polyline) >= 2
    )


def export_plan_gpx(
    plan: TripPlan,
    output_path: str | Path,
    *,
    route_name: str = "骑行食宿规划路线",
    segments_wgs84: tuple[tuple[Coordinate, ...], ...] | None = None,
    source_path: str | Path | None = None,
) -> Path:
    """导出 WGS-84 GPX：保留固定轨迹，并加入推荐的 POI 途经点。"""

    destination = Path(output_path).expanduser()
    if (
        source_path
        and destination.resolve() == Path(source_path).expanduser().resolve()
    ):
        raise GpxError("输出 GPX 不能覆盖导入的原文件")

    segments = segments_wgs84 or _route_segments_as_wgs84(plan.route)
    if not segments:
        raise GpxError("规划结果中没有可导出的路线坐标")

    ET.register_namespace("", GPX_NAMESPACE)
    root = ET.Element(
        "gpx",
        {
            "xmlns": GPX_NAMESPACE,
            "version": "1.1",
            "creator": "Cycling Food Lodging Agent",
        },
    )
    metadata = ET.SubElement(root, "metadata")
    _add_text(metadata, "name", route_name)

    for index, recommendation in enumerate(plan.lunch_recommendations, start=1):
        _add_waypoint(
            root,
            recommendation.poi,
            name_prefix=f"午饭{index}",
            waypoint_type="lunch",
            description=(
                f"推荐分 {recommendation.score:.1f}；"
                f"{_format_cost(recommendation.poi)}；"
                f"{recommendation.poi.address or '地址暂无'}"
            ),
        )

    for stop in plan.rest_stops:
        if stop.poi is None:
            continue
        route_progress_text = (
            f"距起点 {stop.route_progress_meters / 1000:.2f} 公里"
            if stop.route_progress_meters is not None
            else "距起点里程暂无"
        )
        interval_text = (
            f"距上一休息点 {stop.distance_since_previous_meters / 1000:.2f} 公里"
            if stop.distance_since_previous_meters is not None
            else "休息点间隔暂无"
        )
        offset_text = (
            f"偏离路线约 {stop.distance_from_route_meters} 米"
            if stop.distance_from_route_meters is not None
            else "偏离路线距离暂无"
        )
        _add_waypoint(
            root,
            stop.poi,
            name_prefix=f"休息点{stop.sequence}",
            waypoint_type="rest",
            description=(
                f"{stop.category or '休息点'}；"
                f"{route_progress_text}；{interval_text}；{offset_text}"
            ),
        )

    exported_pois: set[tuple[str, str]] = set()
    for index, option in enumerate(plan.lodging_options, start=1):
        lodging = option.lodging.poi
        lodging_key = ("lodging", lodging.poi_id or lodging.name)
        if lodging_key not in exported_pois:
            exported_pois.add(lodging_key)
            _add_waypoint(
                root,
                lodging,
                name_prefix=f"住宿{index}",
                waypoint_type="lodging",
                description=f"{_format_cost(lodging)}；{lodging.address or '地址暂无'}",
            )

        if option.dinner is None:
            continue
        dinner = option.dinner.poi
        dinner_key = ("dinner", dinner.poi_id or dinner.name)
        if dinner_key in exported_pois:
            continue
        exported_pois.add(dinner_key)
        road_distance_text = (
            f"住宿到晚饭道路距离 "
            f"{option.dinner_road_distance_meters / 1000:.2f} 公里"
            if option.dinner_road_distance_meters is not None
            else "住宿到晚饭道路距离暂无"
        )
        _add_waypoint(
            root,
            dinner,
            name_prefix=f"晚饭{index}",
            waypoint_type="dinner",
            description=(
                f"{_format_cost(dinner)}；{road_distance_text}；"
                f"{dinner.address or '地址暂无'}"
            ),
        )

    track = ET.SubElement(root, "trk")
    _add_text(track, "name", route_name)
    for segment in segments:
        track_segment = ET.SubElement(track, "trkseg")
        for longitude, latitude in segment:
            ET.SubElement(
                track_segment,
                "trkpt",
                {"lat": f"{latitude:.8f}", "lon": f"{longitude:.8f}"},
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return destination
