import os

import requests
from dotenv import load_dotenv


load_dotenv()

AMAP_API_KEY = os.getenv("AMAP_API_KEY")

if not AMAP_API_KEY:
    raise RuntimeError("没有找到 AMAP_API_KEY，请检查 .env 文件")


def geocode(address, city):
    """将地点名称转换为经纬度。"""

    url = "https://restapi.amap.com/v3/geocode/geo"

    params = {
        "key": AMAP_API_KEY,
        "address": address,
        "city": city,
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()

    if data.get("status") != "1":
        raise RuntimeError(f"地理编码失败：{data.get('info')}")

    geocodes = data.get("geocodes", [])

    if not geocodes:
        return None

    first_result = geocodes[0]

    return {
        "address": first_result["formatted_address"],
        "location": first_result["location"],
    }

def extract_route_points(steps):
    """从所有路线分段中提取坐标。"""

    route_points = []

    for step in steps:
        polyline = step.get("polyline", "")

        if not polyline:
            continue

        for coordinate_string in polyline.split(";"):
            longitude_text, latitude_text = coordinate_string.split(",")

            point = (
                float(longitude_text),
                float(latitude_text),
            )

            # 避免相邻分段重复保存连接点
            if not route_points or point != route_points[-1]:
                route_points.append(point)

    return route_points

def get_bicycle_route(origin, destination):
    """根据起终点经纬度查询骑行路线。"""

    url = "https://restapi.amap.com/v5/direction/bicycling"

    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
        "show_fields": "polyline",
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()

    if data.get("status") != "1":
        raise RuntimeError(f"骑行路线查询失败：{data.get('info')}")

    paths = data.get("route", {}).get("paths", [])

    if not paths:
        return None

    first_path = paths[0]

    steps = first_path.get("steps", [])
    route_points = extract_route_points(steps)

    distance_meters = int(first_path["distance"])
    duration_seconds = int(first_path["duration"])

    return {
        "distance_meters": distance_meters,
        "duration_seconds": duration_seconds,
        "route_points": route_points,
    }


def main():
    start_city = input("请输入起点城市：").strip()
    start_name = input("请输入骑行起点：").strip()

    end_city = input("请输入终点城市：").strip()
    end_name = input("请输入骑行终点：").strip()

    if not start_city or not start_name or not end_city or not end_name:
        print("城市、起点和终点不能为空")
        return

    start = geocode(start_name, start_city)
    end = geocode(end_name, end_city)

    if start is None:
        print("没有找到起点")
        return

    if end is None:
        print("没有找到终点")
        return

    print()
    print("起点识别为：", start["address"])
    print("起点坐标：", start["location"])
    print("终点识别为：", end["address"])
    print("终点坐标：", end["location"])

    route = get_bicycle_route(
        start["location"],
        end["location"],
    )

    if route is None:
        print("没有找到可用的骑行路线")
        return

    distance_km = route["distance_meters"] / 1000
    duration_hours = route["duration_seconds"] / 3600

    print()
    print(f"骑行距离：{distance_km:.2f} 公里")
    print(f"预计时间：{duration_hours:.2f} 小时")
    print(f"路线坐标点数量：{len(route['route_points'])}")


if __name__ == "__main__":
    main()