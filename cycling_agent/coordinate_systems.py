"""WGS-84 与高德 GCJ-02 坐标之间的本地转换。"""

import math

from cycling_agent.models import Coordinate


_PI = math.pi
_SEMI_MAJOR_AXIS = 6_378_245.0
_ECCENTRICITY_SQUARED = 0.006693421622965943


def _outside_mainland_china(longitude: float, latitude: float) -> bool:
    return not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271)


def _transform_latitude(longitude: float, latitude: float) -> float:
    result = (
        -100.0
        + 2.0 * longitude
        + 3.0 * latitude
        + 0.2 * latitude * latitude
        + 0.1 * longitude * latitude
        + 0.2 * math.sqrt(abs(longitude))
    )
    result += (
        20.0 * math.sin(6.0 * longitude * _PI)
        + 20.0 * math.sin(2.0 * longitude * _PI)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(latitude * _PI)
        + 40.0 * math.sin(latitude / 3.0 * _PI)
    ) * 2.0 / 3.0
    result += (
        160.0 * math.sin(latitude / 12.0 * _PI)
        + 320 * math.sin(latitude * _PI / 30.0)
    ) * 2.0 / 3.0
    return result


def _transform_longitude(longitude: float, latitude: float) -> float:
    result = (
        300.0
        + longitude
        + 2.0 * latitude
        + 0.1 * longitude * longitude
        + 0.1 * longitude * latitude
        + 0.1 * math.sqrt(abs(longitude))
    )
    result += (
        20.0 * math.sin(6.0 * longitude * _PI)
        + 20.0 * math.sin(2.0 * longitude * _PI)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(longitude * _PI)
        + 40.0 * math.sin(longitude / 3.0 * _PI)
    ) * 2.0 / 3.0
    result += (
        150.0 * math.sin(longitude / 12.0 * _PI)
        + 300.0 * math.sin(longitude / 30.0 * _PI)
    ) * 2.0 / 3.0
    return result


def wgs84_to_gcj02(coordinate: Coordinate) -> Coordinate:
    """GPX/GPS 使用的 WGS-84 坐标转为高德 GCJ-02。"""

    longitude, latitude = coordinate
    if _outside_mainland_china(longitude, latitude):
        return coordinate

    latitude_delta = _transform_latitude(longitude - 105.0, latitude - 35.0)
    longitude_delta = _transform_longitude(longitude - 105.0, latitude - 35.0)
    latitude_radians = latitude / 180.0 * _PI
    sine = math.sin(latitude_radians)
    magic = 1 - _ECCENTRICITY_SQUARED * sine * sine
    square_root_magic = math.sqrt(magic)
    latitude_delta = (
        latitude_delta * 180.0
    ) / (
        (_SEMI_MAJOR_AXIS * (1 - _ECCENTRICITY_SQUARED))
        / (magic * square_root_magic)
        * _PI
    )
    longitude_delta = (
        longitude_delta * 180.0
    ) / (
        _SEMI_MAJOR_AXIS
        / square_root_magic
        * math.cos(latitude_radians)
        * _PI
    )
    return longitude + longitude_delta, latitude + latitude_delta


def gcj02_to_wgs84(coordinate: Coordinate) -> Coordinate:
    """高德 GCJ-02 坐标转回 GPX 使用的 WGS-84。

    采用迭代反解，避免一次近似在长距离轨迹中累积偏差。
    """

    longitude, latitude = coordinate
    if _outside_mainland_china(longitude, latitude):
        return coordinate

    guess_longitude = longitude
    guess_latitude = latitude
    for _ in range(8):
        converted_longitude, converted_latitude = wgs84_to_gcj02(
            (guess_longitude, guess_latitude)
        )
        guess_longitude -= converted_longitude - longitude
        guess_latitude -= converted_latitude - latitude

    return guess_longitude, guess_latitude
