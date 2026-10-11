import unittest

from cycling_agent.models import RouteStep
from cycling_agent.route_progress import lunch_search_points, point_at_road_ratio


class RouteProgressTests(unittest.TestCase):
    def setUp(self):
        # 两段折线看起来一样长，但高德道路里程分别是 600 米和 400 米。
        # 这可以验证代码依据 step_distance，而不是经纬度直线长度。
        self.steps = (
            RouteStep(600, ((0.0, 0.0), (1.0, 0.0))),
            RouteStep(400, ((1.0, 0.0), (2.0, 0.0))),
        )

    def test_halfway_uses_official_road_distance(self):
        longitude, latitude = point_at_road_ratio(self.steps, 0.5)

        self.assertAlmostEqual(longitude, 5 / 6, places=5)
        self.assertAlmostEqual(latitude, 0.0, places=5)

    def test_three_lunch_points_cover_half_to_two_thirds(self):
        search_points = lunch_search_points(self.steps)

        self.assertEqual([ratio for ratio, _ in search_points], [0.5, 7 / 12, 2 / 3])

    def test_invalid_ratio_is_rejected(self):
        with self.assertRaises(ValueError):
            point_at_road_ratio(self.steps, 1.1)


if __name__ == "__main__":
    unittest.main()
