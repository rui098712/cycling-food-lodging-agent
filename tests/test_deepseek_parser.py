import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cycling_agent.deepseek_parser import DeepSeekTripRequestParser
from cycling_agent.llm_parser import TripRequestDraft
from cycling_agent.parser_factory import create_parser


class FakeResponses:
    def __init__(self, draft):
        self.draft = draft
        self.last_arguments = None

    def parse(self, **kwargs):
        self.last_arguments = kwargs
        return SimpleNamespace(output_parsed=self.draft)


class FakeClient:
    def __init__(self, draft):
        self.responses = FakeResponses(draft)


def complete_draft():
    return TripRequestDraft(
        start_city="武威市天祝县",
        start_name="达隆宾馆",
        end_city="张掖市",
        end_name="七彩丹霞景区",
        lunch_standard_per_person=50,
        dinner_standard_per_person=80,
        lodging_standard_per_person=250,
    )


class DeepSeekParserTests(unittest.TestCase):
    def test_parser_uses_schema_and_disables_thinking(self):
        client = FakeClient(complete_draft())
        parser = DeepSeekTripRequestParser(client=client, model="test-deepseek")

        result = parser.parse("从达隆宾馆骑到七彩丹霞")

        self.assertEqual(result.end_name, "七彩丹霞景区")
        arguments = client.responses.last_arguments
        self.assertEqual(arguments["model"], "test-deepseek")
        self.assertIs(arguments["text_format"], TripRequestDraft)
        self.assertEqual(arguments["max_output_tokens"], 1200)
        self.assertEqual(
            arguments["extra_body"],
            {"thinking": {"type": "disabled"}},
        )

    def test_missing_api_key_is_reported_before_network_request(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                DeepSeekTripRequestParser()

    def test_factory_selects_deepseek_provider(self):
        fake_parser = object()
        with patch(
            "cycling_agent.parser_factory.DeepSeekTripRequestParser",
            return_value=fake_parser,
        ) as parser_class:
            provider, parser = create_parser("deepseek")

        self.assertEqual(provider, "deepseek")
        self.assertIs(parser, fake_parser)
        parser_class.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
