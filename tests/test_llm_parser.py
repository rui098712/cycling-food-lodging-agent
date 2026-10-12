import unittest
from types import SimpleNamespace

from cycling_agent.llm_agent import LLMCyclingAgent
from cycling_agent.llm_parser import (
    LLMParsingError,
    LLMTripRequestParser,
    MissingTripFieldsError,
    TripRequestDraft,
)
from cycling_agent.models import BicycleRoute, GeoPlace, RouteStep, TripPlan, TripRequest
from llm_main import complete_request


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.last_arguments = None

    def parse(self, **kwargs):
        self.last_arguments = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class FakeOpenAIClient:
    def __init__(self, parsed):
        self.responses = FakeResponses(parsed)


class FakeParser:
    def __init__(self, draft):
        self.draft = draft

    def parse(self, user_text):
        return self.draft


class FakePlanner:
    def __init__(self):
        self.received_request = None

    def plan(self, request):
        self.received_request = request
        route = BicycleRoute(
            distance_meters=1000,
            duration_seconds=300,
            steps=(RouteStep(1000, ((0.0, 0.0), (0.01, 0.01))),),
        )
        return TripPlan(
            start=GeoPlace("测试起点", (0.0, 0.0)),
            end=GeoPlace("测试终点", (0.01, 0.01)),
            route=route,
            lunch_search_point_count=1,
        )


def complete_draft():
    return TripRequestDraft(
        start_city="广州市",
        start_name="广州塔",
        end_city="深圳市",
        end_name="深圳湾公园",
        lunch_standard_per_person=50,
        dinner_standard_per_person=80,
        lodging_standard_per_person=250,
    )


class LLMParserTests(unittest.TestCase):
    def test_parser_uses_responses_structured_output(self):
        draft = complete_draft()
        client = FakeOpenAIClient(draft)
        parser = LLMTripRequestParser(client=client, model="test-model")

        result = parser.parse("从广州塔骑到深圳湾公园")

        self.assertEqual(result, draft)
        arguments = client.responses.last_arguments
        self.assertEqual(arguments["model"], "test-model")
        self.assertIs(arguments["text_format"], TripRequestDraft)
        self.assertEqual(arguments["input"][-1]["role"], "user")
        self.assertEqual(
            set(TripRequestDraft.model_json_schema()["required"]),
            set(draft.model_dump()),
        )

    def test_missing_fields_must_be_completed_before_planning(self):
        draft = TripRequestDraft(
            start_city="广州市",
            start_name="广州塔",
            end_city="深圳市",
            end_name="深圳湾公园",
            lunch_standard_per_person=None,
            dinner_standard_per_person=80,
            lodging_standard_per_person=None,
        )

        self.assertEqual(
            draft.missing_fields(),
            (
                "lunch_standard_per_person",
                "lodging_standard_per_person",
            ),
        )
        with self.assertRaises(MissingTripFieldsError):
            draft.to_trip_request()

        request = draft.to_trip_request(
            lunch_standard_per_person=50,
            lodging_standard_per_person=250,
        )
        self.assertEqual(request.lunch_standard_per_person, 50)
        self.assertEqual(request.lodging_standard_per_person, 250)

    def test_llm_agent_passes_structured_request_to_existing_planner(self):
        draft = complete_draft()
        planner = FakePlanner()
        agent = LLMCyclingAgent(FakeParser(draft), planner)

        result = agent.plan("测试行程")

        self.assertEqual(result.plan.route.distance_meters, 1000)
        self.assertTrue(result.validation.passed)
        self.assertTrue(result.trace)
        self.assertIsInstance(result.request, TripRequest)
        self.assertEqual(planner.received_request.end_name, "深圳湾公园")

    def test_blank_user_text_is_rejected_without_api_call(self):
        client = FakeOpenAIClient(complete_draft())
        parser = LLMTripRequestParser(client=client)

        with self.assertRaises(ValueError):
            parser.parse("   ")

        self.assertIsNone(client.responses.last_arguments)

    def test_missing_structured_output_is_reported(self):
        parser = LLMTripRequestParser(client=FakeOpenAIClient(None))

        with self.assertRaises(LLMParsingError):
            parser.parse("测试行程")

    def test_gpx_mode_can_fill_missing_place_names_from_track(self):
        draft = TripRequestDraft(
            start_city=None,
            start_name=None,
            end_city=None,
            end_name=None,
            lunch_standard_per_person=50,
            dinner_standard_per_person=80,
            lodging_standard_per_person=250,
        )

        request = complete_request(draft, gpx_route_name="河西走廊")

        self.assertEqual(request.start_city, "GPX")
        self.assertEqual(request.start_name, "河西走廊轨迹起点")
        self.assertEqual(request.end_name, "河西走廊轨迹终点")


if __name__ == "__main__":
    unittest.main()
