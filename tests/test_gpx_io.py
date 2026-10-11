import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from cycling_agent.coordinate_systems import gcj02_to_wgs84, wgs84_to_gcj02
from cycling_agent.gpx_io import GpxError, export_plan_gpx, import_gpx
from cycling_agent.models import (
    GeoPlace,
    LodgingOption,
    Poi,
    Recommendation,
    RestStopPlan,
    TripPlan,
)


SAMPLE_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>测试固定路线</name></metadata>
  <trk>
    <name>测试固定路线</name>
    <trkseg>
      <trkpt lat="23.106428" lon="113.324521" />
      <trkpt lat="23.116428" lon="113.334521" />
      <trkpt lat="23.126428" lon="113.344521" />
    </trkseg>
  </trk>
</gpx>
"""


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


class GpxIoTests(unittest.TestCase):
    def test_coordinate_conversion_round_trip(self):
        original = (113.324521, 23.106428)

        amap_coordinate = wgs84_to_gcj02(original)
        restored = gcj02_to_wgs84(amap_coordinate)

        self.assertNotEqual(amap_coordinate, original)
        self.assertAlmostEqual(restored[0], original[0], places=6)
        self.assertAlmostEqual(restored[1], original[1], places=6)

    def test_import_uses_track_as_fixed_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "route.gpx"
            path.write_text(SAMPLE_GPX, encoding="utf-8")

            imported = import_gpx(path)

        self.assertEqual(imported.name, "测试固定路线")
        self.assertEqual(imported.point_count, 3)
        self.assertEqual(imported.route.source, "gpx")
        self.assertGreater(imported.route.distance_meters, 2_000)
        self.assertNotEqual(
            imported.route.steps[0].polyline[0],
            imported.segments_wgs84[0][0],
        )

    def test_export_preserves_track_and_adds_recommendation_waypoints(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "route.gpx"
            output = Path(directory) / "route_planned.gpx"
            source.write_text(SAMPLE_GPX, encoding="utf-8")
            imported = import_gpx(source)

            lunch_poi = Poi("lunch", "测试午饭", "午饭地址", imported.route.steps[0].polyline[1], cost=50)
            rest_poi = Poi("rest", "测试加油站", "休息地址", imported.route.steps[0].polyline[1])
            lodging_poi = Poi("hotel", "测试宾馆", "住宿地址", imported.route.steps[0].polyline[-1])
            dinner_poi = Poi("dinner", "测试晚饭", "晚饭地址", imported.route.steps[0].polyline[-1])
            plan = TripPlan(
                start=GeoPlace("GPX 起点", imported.route.steps[0].polyline[0]),
                end=GeoPlace("GPX 终点", imported.route.steps[0].polyline[-1]),
                route=imported.route,
                lunch_search_point_count=2,
                lunch_recommendations=(Recommendation(lunch_poi, 90, ("测试",)),),
                rest_stops=(
                    RestStopPlan(
                        sequence=1,
                        search_start_meters=15_000,
                        search_end_meters=20_000,
                        search_mode="preferred",
                        poi=rest_poi,
                        category="加油站",
                        route_progress_meters=17_000,
                        distance_from_route_meters=20,
                        distance_since_previous_meters=17_000,
                    ),
                ),
                lodging_options=(
                    LodgingOption(
                        lodging=Recommendation(lodging_poi, 80, ("测试",)),
                        dinner=Recommendation(dinner_poi, 70, ("测试",)),
                        dinner_road_distance_meters=500,
                    ),
                ),
            )

            written = export_plan_gpx(
                plan,
                output,
                segments_wgs84=imported.segments_wgs84,
                source_path=source,
            )
            root = ET.parse(written).getroot()
            waypoint_types = {
                child.text
                for item in root.iter()
                if local_name(item.tag) == "wpt"
                for child in item
                if local_name(child.tag) == "type"
            }
            exported_track_points = [
                (float(item.attrib["lon"]), float(item.attrib["lat"]))
                for item in root.iter()
                if local_name(item.tag) == "trkpt"
            ]

            self.assertEqual(
                waypoint_types,
                {"lunch", "rest", "lodging", "dinner"},
            )
            self.assertEqual(exported_track_points, list(imported.segments_wgs84[0]))
            self.assertEqual(import_gpx(output).point_count, 3)

    def test_export_refuses_to_overwrite_imported_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "route.gpx"
            source.write_text(SAMPLE_GPX, encoding="utf-8")
            imported = import_gpx(source)
            plan = TripPlan(
                start=GeoPlace("GPX 起点", imported.route.steps[0].polyline[0]),
                end=GeoPlace("GPX 终点", imported.route.steps[0].polyline[-1]),
                route=imported.route,
                lunch_search_point_count=2,
            )

            with self.assertRaises(GpxError):
                export_plan_gpx(
                    plan,
                    source,
                    segments_wgs84=imported.segments_wgs84,
                    source_path=source,
                )


if __name__ == "__main__":
    unittest.main()
