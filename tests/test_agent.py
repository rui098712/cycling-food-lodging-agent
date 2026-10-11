import unittest

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.models import BicycleRoute, GeoPlace, Poi, RouteStep, TripRequest


class FakeAmapClient:
    """离线替代高德客户端，用于验证 Agent 的完整工具调用流程。"""

    def __init__(self):
        self.search_calls = []

    def geocode(self, address, city):
        if address == "起点":
            return GeoPlace(f"{city}{address}", (0.0, 0.0))
        return GeoPlace(f"{city}{address}", (2.0, 0.0))

    def bicycle_route(self, origin, destination):
        return BicycleRoute(
            distance_meters=1000,
            duration_seconds=600,
            steps=(
                RouteStep(600, ((0.0, 0.0), (1.0, 0.0))),
                RouteStep(400, ((1.0, 0.0), (2.0, 0.0))),
            ),
        )

    def search_nearby(self, location, *, types, radius, route_ratio=None, page_size=20):
        self.search_calls.append((location, types, radius, route_ratio))
        kind = "餐厅" if types == "050000" else "住宿"
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

        self.assertEqual(plan.route.distance_meters, 1000)
        self.assertEqual(len(amap.search_calls), 5)
        self.assertEqual(
            [call[3] for call in amap.search_calls[:3]],
            [0.5, 7 / 12, 2 / 3],
        )
        self.assertEqual(amap.search_calls[3][1], "050000")
        self.assertEqual(amap.search_calls[4][1], "100000")
        self.assertTrue(plan.lunch_recommendations)
        self.assertTrue(plan.dinner_recommendations)
        self.assertTrue(plan.lodging_recommendations)


if __name__ == "__main__":
    unittest.main()
