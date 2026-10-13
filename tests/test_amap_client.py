import unittest
from unittest.mock import patch

from cycling_agent.amap_client import AmapClient


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {"status": "1", "value": "ok"}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses=None):
        self.call_count = 0
        self.responses = list(responses or [FakeResponse()])

    def get(self, url, params, timeout):
        self.call_count += 1
        index = min(self.call_count - 1, len(self.responses) - 1)
        return self.responses[index]


class AmapClientTests(unittest.TestCase):
    def test_successful_identical_request_is_cached(self):
        client = AmapClient("test-key")
        fake_session = FakeSession()
        client._thread_local.session = fake_session

        first = client._get("https://example.com/test", {"city": "广州"})
        second = client._get("https://example.com/test", {"city": "广州"})

        self.assertEqual(first, second)
        self.assertEqual(fake_session.call_count, 1)

    def test_https_session_has_retries(self):
        client = AmapClient("test-key", max_retries=3)

        adapter = client.session.get_adapter("https://")

        self.assertEqual(adapter.max_retries.total, 3)
        self.assertIn(503, adapter.max_retries.status_forcelist)

    def test_reverse_geocode_parses_administrative_area(self):
        client = AmapClient("test-key", max_requests_per_second=1000)
        client._thread_local.session = FakeSession(
            responses=(
                FakeResponse(
                    {
                        "status": "1",
                        "regeocode": {
                            "addressComponent": {
                                "province": "甘肃省",
                                "city": "张掖市",
                                "district": "临泽县",
                                "township": "倪家营镇",
                            }
                        },
                    }
                ),
            )
        )

        area = client.reverse_geocode((100.1, 38.9))

        self.assertEqual(area.city_name, "张掖市")
        self.assertEqual(area.district_name, "临泽县")
        self.assertEqual(area.township_name, "倪家营镇")

    def test_reverse_geocode_uses_municipality_as_city(self):
        client = AmapClient("test-key", max_requests_per_second=1000)
        client._thread_local.session = FakeSession(
            responses=(
                FakeResponse(
                    {
                        "status": "1",
                        "regeocode": {
                            "addressComponent": {
                                "province": "北京市",
                                "city": [],
                                "district": "海淀区",
                                "township": "燕园街道",
                            }
                        },
                    }
                ),
            )
        )

        area = client.reverse_geocode((116.31, 39.99))

        self.assertEqual(area.city_name, "北京市")

    @patch("cycling_agent.amap_client.time.sleep")
    def test_qps_limit_is_retried(self, mocked_sleep):
        client = AmapClient(
            "test-key",
            max_retries=2,
            max_requests_per_second=1000,
        )
        fake_session = FakeSession(
            responses=(
                FakeResponse(
                    {
                        "status": "0",
                        "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                        "infocode": "10021",
                    }
                ),
                FakeResponse(),
            )
        )
        client._thread_local.session = fake_session

        data = client._get("https://example.com/test", {"city": "广州"})

        self.assertEqual(data["status"], "1")
        self.assertEqual(fake_session.call_count, 2)
        self.assertTrue(mocked_sleep.called)

    @patch("cycling_agent.amap_client.time.sleep")
    def test_engine_response_error_is_retried(self, mocked_sleep):
        client = AmapClient(
            "test-key",
            max_retries=2,
            max_requests_per_second=1000,
        )
        fake_session = FakeSession(
            responses=(
                FakeResponse(
                    {
                        "status": "0",
                        "info": "RESULTS_ARE_EMPTY",
                        "infocode": "30007",
                    }
                ),
                FakeResponse(),
            )
        )
        client._thread_local.session = fake_session

        data = client._get("https://example.com/test", {"city": "广州"})

        self.assertEqual(data["status"], "1")
        self.assertEqual(fake_session.call_count, 2)
        self.assertTrue(mocked_sleep.called)

    @patch("cycling_agent.amap_client.time.sleep")
    def test_long_bicycle_route_is_split_and_merged(self, mocked_sleep):
        route_response = FakeResponse(
            {
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "1000",
                            "duration": "600",
                            "steps": [
                                {
                                    "step_distance": "1000",
                                    "polyline": "116.0,40.0;117.0,39.0",
                                    "instruction": "测试路段",
                                    "road_name": "测试路",
                                }
                            ],
                        }
                    ]
                },
            }
        )
        client = AmapClient(
            "test-key",
            max_requests_per_second=1000,
        )
        fake_session = FakeSession(responses=(route_response,))
        client._thread_local.session = fake_session

        route = client.bicycle_route((116.0, 40.0), (128.0, 30.0))

        self.assertGreater(route.planning_segment_count, 1)
        self.assertEqual(
            fake_session.call_count,
            route.planning_segment_count,
        )
        self.assertEqual(
            route.distance_meters,
            1000 * route.planning_segment_count,
        )
        self.assertEqual(
            route.duration_seconds,
            600 * route.planning_segment_count,
        )
        self.assertEqual(len(route.steps), route.planning_segment_count)


if __name__ == "__main__":
    unittest.main()
