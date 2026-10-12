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

    def test_town_or_urban_lunch_candidate_gets_priority(self):
        pois = (
            Poi(
                "rural",
                "沿路饭店",
                "",
                (0, 0),
                rating=4.5,
                cost=45,
                distance_meters=300,
            ),
            Poi(
                "town",
                "镇区餐厅",
                "",
                (0, 0),
                rating=4.5,
                cost=45,
                distance_meters=300,
                township_name="倪家营镇",
                lunch_area_kind="town",
            ),
        )

        results = rank_pois(pois, spending_standard_per_person=50)

        self.assertEqual(results[0].poi.name, "镇区餐厅")
        self.assertIn("午饭优先", "".join(results[0].reasons))
        self.assertEqual(results[0].score - results[1].score, 12.0)


if __name__ == "__main__":
    unittest.main()
