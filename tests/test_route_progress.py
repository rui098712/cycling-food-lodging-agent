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

    def test_lunch_points_adapt_to_route_length(self):
        long_steps = (
            RouteStep(72_000, ((0.0, 0.0), (1.0, 0.0))),
            RouteStep(48_000, ((1.0, 0.0), (2.0, 0.0))),
        )
        search_points = lunch_search_points(long_steps, max_road_gap_meters=4000)

        ratios = [ratio for ratio, _ in search_points]
        self.assertEqual(len(ratios), 6)
        self.assertAlmostEqual(ratios[0], 0.5)
        self.assertAlmostEqual(ratios[-1], 2 / 3)
        road_gaps = [
            (ratios[index] - ratios[index - 1]) * 120_000
            for index in range(1, len(ratios))
        ]
        self.assertTrue(all(gap <= 4000 for gap in road_gaps))

    def test_invalid_ratio_is_rejected(self):
        with self.assertRaises(ValueError):
            point_at_road_ratio(self.steps, 1.1)


if __name__ == "__main__":
    unittest.main()
