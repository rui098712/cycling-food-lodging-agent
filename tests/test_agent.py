import unittest

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.models import BicycleRoute, GeoPlace, Poi, RouteStep, TripRequest


class FakeAmapClient:
    """离线替代高德客户端，用于验证 Agent 的完整工具调用流程。"""

    def __init__(self, pair_distance=1200):
        self.search_calls = []
        self.distance_calls = []
        self.pair_distance = pair_distance

    def geocode(self, address, city):
        if address == "起点":
            return GeoPlace(f"{city}{address}", (0.0, 0.0))
        return GeoPlace(f"{city}{address}", (2.0, 0.0))

    def bicycle_route(self, origin, destination):
        return BicycleRoute(
            distance_meters=120_000,
            duration_seconds=36_000,
            steps=(
                RouteStep(72_000, ((0.0, 0.0), (1.0, 0.0))),
                RouteStep(48_000, ((1.0, 0.0), (2.0, 0.0))),
            ),
        )

    def bicycle_distance(self, origin, destination):
        self.distance_calls.append((origin, destination))
        return self.pair_distance

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
        self.search_calls.append((location, types, radius, route_ratio))
        if types == "010100":
            kind = "测试加油站"
        elif types == "060000":
            kind = "测试便利店"
        elif types == "050000":
            kind = "测试餐厅"
        else:
            kind = "测试酒店"
        return (
            Poi(
                poi_id=f"{kind}-{route_ratio}",
                name=kind,
                address="测试地址",
                location=location,
                rating=4.5,
                cost=40,
                distance_meters=200,
                route_ratio=route_ratio,
            ),
        )


class AgentWorkflowTests(unittest.TestCase):
    def test_agent_calls_route_and_poi_tools_in_expected_order(self):
        amap = FakeAmapClient()
        agent = CyclingPlannerAgent(amap)
        request = TripRequest("甲市", "起点", "乙市", "终点", 50, 60, 200)

        plan = agent.plan(request)

        self.assertEqual(plan.route.distance_meters, 120_000)
        self.assertEqual(plan.lunch_search_point_count, 6)
        lunch_calls = [call for call in amap.search_calls if call[3] is not None]
        lunch_ratios = [call[3] for call in lunch_calls]
        self.assertEqual(len(lunch_ratios), 6)
        self.assertAlmostEqual(lunch_ratios[0], 0.5)
        self.assertAlmostEqual(lunch_ratios[-1], 2 / 3)
        self.assertTrue(any(call[1] == "010100" for call in amap.search_calls))
        self.assertTrue(plan.lunch_recommendations)
        self.assertTrue(plan.rest_stops)
        self.assertTrue(any(stop.poi is not None for stop in plan.rest_stops))
        self.assertTrue(plan.lodging_options)
        self.assertIsNotNone(plan.lodging_options[0].dinner)
        self.assertEqual(
            plan.lodging_options[0].dinner_road_distance_meters,
            1200,
        )
        self.assertEqual(len(amap.distance_calls), 1)

    def test_pair_over_three_kilometers_is_rejected(self):
        amap = FakeAmapClient(pair_distance=3001)
        agent = CyclingPlannerAgent(amap)
        request = TripRequest("甲市", "起点", "乙市", "终点", 50, 60, 200)

        plan = agent.plan(request)

        self.assertTrue(plan.lodging_options)
        self.assertIsNone(plan.lodging_options[0].dinner)
        self.assertIsNone(plan.lodging_options[0].dinner_road_distance_meters)
        self.assertEqual(len(amap.distance_calls), 1)


if __name__ == "__main__":
    unittest.main()
