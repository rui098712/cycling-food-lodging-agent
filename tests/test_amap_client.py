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


if __name__ == "__main__":
    unittest.main()
