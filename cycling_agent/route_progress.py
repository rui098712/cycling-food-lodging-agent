"""按照路线分段里程，在骑行路线中定位坐标。"""

import math
from dataclasses import dataclass

from cycling_agent.models import Coordinate, RouteStep


@dataclass(frozen=True)
class RouteProjection:
    route_progress_meters: float
    distance_from_route_meters: float
    route_point: Coordinate


def coordinate_distance_meters(point1: Coordinate, point2: Coordinate) -> float:
    """计算两坐标的球面直线距离，用于折线插值或候选预筛选。"""

    longitude1, latitude1 = point1
    longitude2, latitude2 = point2
    earth_radius = 6_371_000

    latitude1_radians = math.radians(latitude1)
    latitude2_radians = math.radians(latitude2)
    delta_latitude = latitude2_radians - latitude1_radians
    delta_longitude = math.radians(longitude2 - longitude1)

    value = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude1_radians)
        * math.cos(latitude2_radians)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * earth_radius * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _point_inside_step(step: RouteStep, ratio: float) -> Coordinate:
    """在已经选中的道路分段内，沿折线找到对应比例的坐标。"""

    points = step.polyline
    if not points:
        raise ValueError("路线分段缺少 polyline，无法定位坐标")
    if len(points) == 1:
        return points[0]

    segment_lengths = [
        coordinate_distance_meters(points[index - 1], points[index])
        for index in range(1, len(points))
    ]
    polyline_length = sum(segment_lengths)

    if polyline_length == 0:
        return points[0]

    target_length = polyline_length * ratio
    walked_length = 0.0

    for index, segment_length in enumerate(segment_lengths):
        if walked_length + segment_length >= target_length:
            local_ratio = (
                (target_length - walked_length) / segment_length
                if segment_length > 0
                else 0
            )
            longitude1, latitude1 = points[index]
            longitude2, latitude2 = points[index + 1]
            return (
                longitude1 + (longitude2 - longitude1) * local_ratio,
                latitude1 + (latitude2 - latitude1) * local_ratio,
            )

        walked_length += segment_length

    return points[-1]


def route_road_distance(steps: tuple[RouteStep, ...]) -> int:
    """返回可定位分段的道路或轨迹里程总和。"""

    return sum(
        step.distance_meters
        for step in steps
        if step.distance_meters > 0 and step.polyline
    )


def point_at_road_distance(
    steps: tuple[RouteStep, ...],
    target_distance_meters: float,
) -> Coordinate:
    """按累计道路或轨迹里程返回对应的路线坐标。"""

    road_distance = route_road_distance(steps)
    if road_distance <= 0:
        raise ValueError("路线没有可用的道路分段")
    if not 0 <= target_distance_meters <= road_distance:
        raise ValueError("目标道路里程超出路线范围")

    walked_distance = 0
    for step in steps:
        if step.distance_meters <= 0 or not step.polyline:
            continue

        step_end_distance = walked_distance + step.distance_meters
        if step_end_distance >= target_distance_meters:
            distance_inside_step = target_distance_meters - walked_distance
            ratio_inside_step = distance_inside_step / step.distance_meters
            return _point_inside_step(step, ratio_inside_step)

        walked_distance = step_end_distance

    valid_steps = tuple(step for step in steps if step.polyline)
    return valid_steps[-1].polyline[-1]


def point_at_road_ratio(steps: tuple[RouteStep, ...], ratio: float) -> Coordinate:
    """按 step_distance 的累计实际道路里程，返回路线某一比例处的坐标。"""

    if not 0 <= ratio <= 1:
        raise ValueError("ratio 必须在 0 到 1 之间")

    road_distance = route_road_distance(steps)
    return point_at_road_distance(steps, road_distance * ratio)


def road_window_search_points(
    steps: tuple[RouteStep, ...],
    start_meters: float,
    end_meters: float,
    max_road_gap_meters: int = 4000,
) -> tuple[tuple[float, Coordinate], ...]:
    """在指定道路里程窗口内生成均匀的 POI 搜索中心。"""

    if max_road_gap_meters <= 0:
        raise ValueError("max_road_gap_meters 必须大于 0")
    if start_meters < 0 or end_meters < start_meters:
        raise ValueError("道路里程窗口不合法")

    total_distance = route_road_distance(steps)
    end_meters = min(end_meters, total_distance)
    if start_meters > end_meters:
        return ()

    window_length = end_meters - start_meters
    interval_count = max(1, math.ceil(window_length / max_road_gap_meters))
    distances = tuple(
        start_meters + window_length * index / interval_count
        for index in range(interval_count + 1)
    )
    return tuple(
        (distance, point_at_road_distance(steps, distance))
        for distance in distances
    )


def _project_to_segment(
    point: Coordinate,
    segment_start: Coordinate,
    segment_end: Coordinate,
) -> tuple[float, Coordinate, float]:
    """把坐标投影到一小段折线上，返回比例、投影坐标和距离。"""

    longitude, latitude = point
    longitude1, latitude1 = segment_start
    longitude2, latitude2 = segment_end
    latitude_scale = 111_320.0
    longitude_scale = latitude_scale * math.cos(
        math.radians((latitude + latitude1 + latitude2) / 3)
    )

    start_x = (longitude1 - longitude) * longitude_scale
    start_y = (latitude1 - latitude) * latitude_scale
    end_x = (longitude2 - longitude) * longitude_scale
    end_y = (latitude2 - latitude) * latitude_scale
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    squared_length = delta_x * delta_x + delta_y * delta_y

    if squared_length == 0:
        ratio = 0.0
    else:
        ratio = -(start_x * delta_x + start_y * delta_y) / squared_length
        ratio = min(max(ratio, 0.0), 1.0)

    projected = (
        longitude1 + (longitude2 - longitude1) * ratio,
        latitude1 + (latitude2 - latitude1) * ratio,
    )
    return ratio, projected, coordinate_distance_meters(point, projected)


def project_point_to_route(
    steps: tuple[RouteStep, ...],
    point: Coordinate,
) -> RouteProjection:
    """求 POI 到路线的最近距离及其对应的累计里程。"""

    best_projection = None
    walked_road_distance = 0.0

    for step in steps:
        if step.distance_meters <= 0 or not step.polyline:
            continue

        points = step.polyline
        if len(points) == 1:
            distance = coordinate_distance_meters(point, points[0])
            projection = RouteProjection(walked_road_distance, distance, points[0])
            if (
                best_projection is None
                or distance < best_projection.distance_from_route_meters
            ):
                best_projection = projection
            walked_road_distance += step.distance_meters
            continue

        segment_lengths = [
            coordinate_distance_meters(points[index - 1], points[index])
            for index in range(1, len(points))
        ]
        geometric_step_length = sum(segment_lengths)
        walked_geometric_distance = 0.0

        for index, segment_length in enumerate(segment_lengths):
            ratio, projected, distance = _project_to_segment(
                point,
                points[index],
                points[index + 1],
            )
            if geometric_step_length > 0:
                step_ratio = (
                    walked_geometric_distance + segment_length * ratio
                ) / geometric_step_length
            else:
                step_ratio = 0.0

            projection = RouteProjection(
                route_progress_meters=(
                    walked_road_distance + step.distance_meters * step_ratio
                ),
                distance_from_route_meters=distance,
                route_point=projected,
            )
            if (
                best_projection is None
                or distance < best_projection.distance_from_route_meters
            ):
                best_projection = projection

            walked_geometric_distance += segment_length

        walked_road_distance += step.distance_meters

    if best_projection is None:
        raise ValueError("路线没有可用于投影的坐标")

    return best_projection


def lunch_search_points(
    steps: tuple[RouteStep, ...],
    max_road_gap_meters: int = 4000,
) -> tuple[tuple[float, Coordinate], ...]:
    """动态覆盖道路里程 1/2～2/3 区间，保证采样点道路间隔不超上限。"""

    if max_road_gap_meters <= 0:
        raise ValueError("max_road_gap_meters 必须大于 0")

    road_distance = route_road_distance(steps)
    if road_distance <= 0:
        raise ValueError("路线没有可用的道路分段")

    start_ratio = 0.5
    end_ratio = 2 / 3
    lunch_interval_meters = road_distance * (end_ratio - start_ratio)
    interval_count = max(1, math.ceil(lunch_interval_meters / max_road_gap_meters))
    ratios = tuple(
        start_ratio + (end_ratio - start_ratio) * index / interval_count
        for index in range(interval_count + 1)
    )
    return tuple((ratio, point_at_road_ratio(steps, ratio)) for ratio in ratios)
