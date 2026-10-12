import tempfile
import unittest
from pathlib import Path

from cycling_agent.agent_runtime import PlanningAgentRuntime
from cycling_agent.models import BicycleRoute, GeoPlace, RouteStep, TripPlan, TripRequest


class FakePlanner:
    def plan(self, request):
        route = BicycleRoute(
            distance_meters=20_000,
            duration_seconds=3600,
            steps=(RouteStep(20_000, ((113.0, 23.0), (113.2, 23.2))),),
        )
        return TripPlan(
            start=GeoPlace("起点", (113.0, 23.0)),
            end=GeoPlace("终点", (113.2, 23.2)),
            route=route,
            lunch_search_point_count=2,
        )

    def plan_with_route(self, request, route):
        plan = self.plan(request)
        return TripPlan(
            start=plan.start,
            end=plan.end,
            route=route,
            lunch_search_point_count=2,
        )


def request():
    return TripRequest("甲市", "起点", "乙市", "终点", 50, 80, 250)


class AgentRuntimeTests(unittest.TestCase):
    def test_agent_selects_tools_until_validated(self):
        result = PlanningAgentRuntime(FakePlanner()).run(request())

        decisions = [
            event.name for event in result.trace if event.event_type == "decision"
        ]
        self.assertEqual(decisions, ["plan_trip", "validate_plan"])
        self.assertTrue(result.validation.passed)
        self.assertEqual(result.trace[-1].name, "finish")

    def test_agent_exports_gpx_through_registered_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.gpx"
            result = PlanningAgentRuntime(FakePlanner()).run(
                request(),
                output_path=output,
            )

            self.assertEqual(result.exported_path, output)
            self.assertTrue(output.is_file())
            decisions = [
                event.name for event in result.trace if event.event_type == "decision"
            ]
            self.assertEqual(
                decisions,
                ["plan_trip", "validate_plan", "export_gpx"],
            )

    def test_max_steps_stops_runaway_agent(self):
        runtime = PlanningAgentRuntime(FakePlanner(), max_steps=1)

        with self.assertRaisesRegex(RuntimeError, "最大执行步数"):
            runtime.run(request())


if __name__ == "__main__":
    unittest.main()
