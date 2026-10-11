import unittest

from cycling_agent.models import BicycleRoute, Poi, RouteStep
from cycling_agent.rest_stop_planner import RestStopPlanner


def point_at_kilometer(kilometers, offset_latitude=0.0):
    # 测试路线长 60 公里，对应经度从 0 到 0.54。
    return 0.54 * kilometers / 60, offset_latitude


def make_poi(poi_id, name, kilometers, offset_latitude=0.0):
    return Poi(
        poi_id=poi_id,
        name=name,
        address="测试地址",
        location=point_at_kilometer(kilometers, offset_latitude),
    )


class FakeRestAmap:
    def __init__(self, gas_pois=(), store_pois=()):
        self.gas_pois = tuple(gas_pois)
        self.store_pois = tuple(store_pois)

    def search_nearby(
        self,
        location,
        *,
        types,
        radius,
        keywords=None,
        route_ratio=None,
        page_size=20,
        sort_rule="weight",
    ):
        if types == "010100":
            return self.gas_pois
        if types == "060000":
            return self.store_pois
        return ()


class RestStopPlannerTests(unittest.TestCase):
    def setUp(self):
        self.route = BicycleRoute(
            distance_meters=60_000,
            duration_seconds=10_000,
            steps=(RouteStep(60_000, ((0.0, 0.0), (0.54, 0.0))),),
        )

    def test_gas_station_is_preferred_in_normal_window(self):
        amap = FakeRestAmap(
            gas_pois=(make_poi("gas", "测试加油站", 18, 0.0004),),
            store_pois=(make_poi("store", "测试便利店", 17.5, 0.0002),),
        )

        results = RestStopPlanner(amap).plan(self.route)

        self.assertEqual(results[0].poi.name, "测试加油站")
        self.assertEqual(results[0].category, "加油站")
        self.assertEqual(results[0].search_mode, "preferred")

    def test_on_route_store_beats_gas_station_that_requires_detour(self):
        amap = FakeRestAmap(
            gas_pois=(make_poi("gas", "测试加油站", 18, 0.0018),),
            store_pois=(make_poi("store", "测试便利店", 17.5, 0.0002),),
        )

        results = RestStopPlanner(amap).plan(self.route)

        self.assertEqual(results[0].poi.name, "测试便利店")
        self.assertEqual(results[0].category, "商店")

    def test_search_extends_to_thirty_kilometers(self):
        amap = FakeRestAmap(
            gas_pois=(make_poi("gas", "远处加油站", 25, 0.0004),),
        )

        results = RestStopPlanner(amap).plan(self.route)

        self.assertEqual(results[0].poi.name, "远处加油站")
        self.assertEqual(results[0].search_mode, "extended")
        self.assertAlmostEqual(results[0].route_progress_meters, 25_000, delta=100)

    def test_candidate_over_three_hundred_meters_is_rejected(self):
        amap = FakeRestAmap(
            gas_pois=(make_poi("gas", "偏离路线的加油站", 18, 0.0031),),
        )

        results = RestStopPlanner(amap).plan(self.route)

        self.assertIsNone(results[0].poi)
        self.assertEqual(results[0].search_mode, "not_found")


if __name__ == "__main__":
    unittest.main()
