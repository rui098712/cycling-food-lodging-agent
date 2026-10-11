import unittest
from types import SimpleNamespace

from cycling_agent.llm_parser import LLMParsingError, TripRequestDraft
from cycling_agent.ollama_parser import OllamaTripRequestParser


class FakeOllamaClient:
    def __init__(self, content):
        self.content = content
        self.last_arguments = None

    def chat(self, **kwargs):
        self.last_arguments = kwargs
        return SimpleNamespace(
            message=SimpleNamespace(content=self.content)
        )


def valid_content():
    return TripRequestDraft(
        start_city="武威市天祝藏族自治县",
        start_name="达隆宾馆",
        end_city="张掖市",
        end_name="七彩丹霞景区",
        lunch_budget=50,
        dinner_budget=80,
        lodging_budget=250,
    ).model_dump_json()


class OllamaParserTests(unittest.TestCase):
    def test_parser_requests_json_schema_and_validates_result(self):
        client = FakeOllamaClient(valid_content())
        parser = OllamaTripRequestParser(client=client, model="test-model")

        draft = parser.parse("从达隆宾馆骑到七彩丹霞")

        self.assertEqual(draft.start_name, "达隆宾馆")
        self.assertEqual(draft.lodging_budget, 250)
        arguments = client.last_arguments
        self.assertEqual(arguments["model"], "test-model")
        self.assertEqual(
            arguments["format"], TripRequestDraft.model_json_schema()
        )
        self.assertEqual(arguments["options"], {"temperature": 0})
        self.assertFalse(arguments["think"])

    def test_invalid_model_output_is_reported(self):
        parser = OllamaTripRequestParser(
            client=FakeOllamaClient("not json"), model="test-model"
        )

        with self.assertRaises(LLMParsingError):
            parser.parse("测试行程")

    def test_blank_input_does_not_call_model(self):
        client = FakeOllamaClient(valid_content())
        parser = OllamaTripRequestParser(client=client)

        with self.assertRaises(ValueError):
            parser.parse("  ")

        self.assertIsNone(client.last_arguments)


if __name__ == "__main__":
    unittest.main()
