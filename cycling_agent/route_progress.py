"""按照高德提供的道路里程，在骑行路线中定位坐标。"""

import math

from cycling_agent.models import Coordinate, RouteStep


def _segment_length(point1: Coordinate, point2: Coordinate) -> float:
    """仅用于在一条 step 的折线内部计算各小段的相对长度。"""

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
        _segment_length(points[index - 1], points[index])
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


def point_at_road_ratio(steps: tuple[RouteStep, ...], ratio: float) -> Coordinate:
    """按 step_distance 的累计实际道路里程，返回路线某一比例处的坐标。"""

    if not 0 <= ratio <= 1:
        raise ValueError("ratio 必须在 0 到 1 之间")

    valid_steps = tuple(
        step for step in steps if step.distance_meters > 0 and step.polyline
    )
    if not valid_steps:
        raise ValueError("路线没有可用的道路分段")

    # 这里的总里程和累计里程都来自高德 step_distance，而不是经纬度直线距离。
    road_distance = sum(step.distance_meters for step in valid_steps)
    target_distance = road_distance * ratio
    walked_distance = 0

    for step in valid_steps:
        step_end_distance = walked_distance + step.distance_meters
        if step_end_distance >= target_distance:
            distance_inside_step = target_distance - walked_distance
            ratio_inside_step = distance_inside_step / step.distance_meters
            return _point_inside_step(step, ratio_inside_step)

        walked_distance = step_end_distance

    return valid_steps[-1].polyline[-1]


def lunch_search_points(steps: tuple[RouteStep, ...]) -> tuple[tuple[float, Coordinate], ...]:
    """在道路里程 1/2 到 2/3 之间均匀选取三个午饭搜索中心。"""

    ratios = (0.5, 7 / 12, 2 / 3)
    return tuple((ratio, point_at_road_ratio(steps, ratio)) for ratio in ratios)
