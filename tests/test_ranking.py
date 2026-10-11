import unittest

from cycling_agent.models import Poi
from cycling_agent.ranking import rank_pois


class RankingTests(unittest.TestCase):
    def test_poi_within_per_person_standard_ranks_first(self):
        pois = (
            Poi("1", "店 A", "", (0, 0), rating=4.8, cost=35, distance_meters=300),
            Poi("2", "店 B", "", (0, 0), rating=4.0, cost=80, distance_meters=300),
        )

        results = rank_pois(pois, spending_standard_per_person=50)

        self.assertEqual(results[0].poi.name, "店 A")
        self.assertIn("符合人均标准", "".join(results[0].reasons))


if __name__ == "__main__":
    unittest.main()
