import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cycling_agent.conversation_agent import (
    ConversationAgent,
    ConversationManager,
    ModelReply,
    ModelToolCall,
)
from cycling_agent.models import (
    AdministrativeArea,
    BicycleRoute,
    GeoPlace,
    RouteStep,
    TripPlan,
)
from cycling_agent.web_service import PlanningJobManager
from web_main import create_app


SAMPLE_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>web-test</name></metadata>
  <trk><trkseg>
    <trkpt lat="23.106428" lon="113.324521" />
    <trkpt lat="23.126428" lon="113.344521" />
  </trkseg></trk>
</gpx>
"""


class FakePlanner:
    def _result(self, route):
        first = route.steps[0].polyline[0]
        last = route.steps[-1].polyline[-1]
        return TripPlan(
            start=GeoPlace("测试起点", first),
            end=GeoPlace("测试终点", last),
            route=route,
            lunch_search_point_count=3,
            lunch_areas=(
                AdministrativeArea(
                    province_name="测试省",
                    city_name="测试市",
                    district_name="测试县",
                    township_name="测试镇",
                ),
            ),
        )

    def plan(self, request):
        return self._result(
            BicycleRoute(
                distance_meters=10_000,
                duration_seconds=3600,
                steps=(
                    RouteStep(
                        10_000,
                        ((113.324521, 23.106428), (113.344521, 23.126428)),
                    ),
                ),
            )
        )

    def plan_with_route(self, request, route):
        return self._result(route)


class WebApiTests(unittest.TestCase):
    def _wait_for_completion(self, client, status_url):
        for _ in range(100):
            response = client.get(status_url)
            payload = response.json()
            if payload["status"] in {"completed", "failed"}:
                return response, payload
            time.sleep(0.01)
        self.fail("规划任务没有在测试时限内完成")

    def test_planning_jobs_are_serial_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PlanningJobManager(directory, planner_factory=FakePlanner)
            try:
                self.assertEqual(manager.max_workers, 1)
            finally:
                manager.shutdown()

    def test_place_form_returns_json_and_downloadable_gpx(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PlanningJobManager(
                directory,
                planner_factory=FakePlanner,
                max_workers=1,
            )
            with TestClient(create_app(manager)) as client:
                created = client.post(
                    "/api/plans",
                    data={
                        "start_city": "甲市",
                        "start_name": "起点",
                        "end_city": "乙市",
                        "end_name": "终点",
                        "lunch_standard_per_person": "50",
                        "dinner_standard_per_person": "80",
                        "lodging_standard_per_person": "250",
                    },
                )
                self.assertEqual(created.status_code, 202)

                response, payload = self._wait_for_completion(
                    client,
                    created.json()["status_url"],
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(payload["status"], "completed")
                self.assertEqual(payload["result"]["route"]["distance_meters"], 10_000)
                self.assertEqual(
                    payload["result"]["route"]["planning_segment_count"],
                    1,
                )
                self.assertEqual(payload["result"]["lunch_recommendations"], [])
                self.assertEqual(
                    payload["result"]["lunch_search_areas"]["cities"],
                    ["测试市"],
                )
                self.assertEqual(
                    payload["result"]["lunch_search_areas"][
                        "townships_and_streets"
                    ],
                    ["测试镇"],
                )

                download = client.get(payload["gpx_download_url"])
                self.assertEqual(download.status_code, 200)
                self.assertIn(b"<gpx", download.content)

    def test_gpx_upload_uses_fixed_route_without_location_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PlanningJobManager(
                directory,
                planner_factory=FakePlanner,
                max_workers=1,
            )
            with TestClient(create_app(manager)) as client:
                created = client.post(
                    "/api/plans",
                    data={
                        "lunch_standard_per_person": "50",
                        "dinner_standard_per_person": "80",
                        "lodging_standard_per_person": "250",
                    },
                    files={
                        "gpx_file": (
                            "route.gpx",
                            SAMPLE_GPX,
                            "application/gpx+xml",
                        )
                    },
                )
                _, payload = self._wait_for_completion(
                    client,
                    created.json()["status_url"],
                )

                self.assertEqual(payload["status"], "completed")
                self.assertEqual(payload["result"]["route"]["source"], "gpx")
                self.assertGreater(payload["result"]["route"]["distance_meters"], 2_000)

    def test_rejects_incomplete_place_form(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PlanningJobManager(
                directory,
                planner_factory=FakePlanner,
                max_workers=1,
            )
            with TestClient(create_app(manager)) as client:
                response = client.post(
                    "/api/plans",
                    data={
                        "start_city": "甲市",
                        "lunch_standard_per_person": "50",
                        "dinner_standard_per_person": "80",
                        "lodging_standard_per_person": "250",
                    },
                )
                self.assertEqual(response.status_code, 400)

    def test_chat_endpoint_runs_function_calls_and_returns_job_url(self):
        class ChatModel:
            model = "test-chat"

            def __init__(self):
                self.replies = [
                    ModelReply(
                        None,
                        (
                            ModelToolCall(
                                "update",
                                "update_trip_request",
                                {
                                    "start_city": "甲市",
                                    "start_name": "起点",
                                    "end_city": "乙市",
                                    "end_name": "终点",
                                    "lunch_standard_per_person": 50,
                                    "dinner_standard_per_person": 80,
                                    "lodging_standard_per_person": 250,
                                },
                            ),
                        ),
                    ),
                    ModelReply(
                        None,
                        (ModelToolCall("submit", "submit_trip_plan", {}),),
                    ),
                    ModelReply("已开始规划。"),
                ]

            def complete(self, messages, tools):
                return self.replies.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            planning_manager = PlanningJobManager(
                directory,
                planner_factory=FakePlanner,
                max_workers=1,
            )
            conversation_manager = ConversationManager(
                ConversationAgent(ChatModel(), planning_manager.submit)
            )
            with TestClient(
                create_app(planning_manager, conversation_manager)
            ) as client:
                created = client.post("/api/chat/sessions")
                self.assertEqual(created.status_code, 201)
                session_id = created.json()["session_id"]

                response = client.post(
                    f"/api/chat/sessions/{session_id}/messages",
                    json={"message": "帮我规划这次骑行"},
                )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "submitted")
                self.assertTrue(payload["status_url"].startswith("/api/plans/"))
                self.assertEqual(len(payload["tool_trace"]), 2)


if __name__ == "__main__":
    unittest.main()
