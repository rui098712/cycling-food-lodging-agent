import unittest

from cycling_agent.conversation_agent import (
    ConversationAgent,
    ConversationManager,
    ModelReply,
    ModelToolCall,
    OllamaFunctionModel,
)


class ScriptedModel:
    model = "scripted-test-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((list(messages), tools))
        if not self.replies:
            raise AssertionError("测试模型没有更多预设回复")
        return self.replies.pop(0)


def tool(call_id, name, arguments):
    return ModelReply(
        None,
        (ModelToolCall(call_id, name, arguments),),
    )


class ConversationAgentTests(unittest.TestCase):
    def test_multi_turn_collects_fields_then_submits(self):
        model = ScriptedModel(
            [
                tool(
                    "call-1",
                    "update_trip_request",
                    {
                        "start_city": "武威市天祝县",
                        "start_name": "达隆宾馆",
                        "end_city": "张掖市",
                        "end_name": "七彩丹霞景区",
                    },
                ),
                ModelReply("请再告诉我三项人均标准。"),
                tool(
                    "call-2",
                    "update_trip_request",
                    {
                        "lunch_standard_per_person": 50,
                        "dinner_standard_per_person": 80,
                        "lodging_standard_per_person": 250,
                    },
                ),
                tool("call-3", "submit_trip_plan", {}),
                ModelReply("信息已齐全，已开始规划。"),
            ]
        )
        submissions = []
        manager = ConversationManager(
            ConversationAgent(
                model,
                lambda submission: submissions.append(submission) or "job-123",
            )
        )
        session = manager.create()

        first = manager.send(session.session_id, "从天祝达隆宾馆到张掖七彩丹霞")
        second = manager.send(session.session_id, "午饭50，晚饭80，住宿250")

        self.assertEqual(first.status, "collecting")
        self.assertIn("lunch_standard_per_person", first.missing_fields)
        self.assertEqual(second.status, "submitted")
        self.assertEqual(second.job_id, "job-123")
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0].lodging_standard_per_person, 250)
        roles = [message["role"] for message in session.messages]
        self.assertIn("tool", roles)

    def test_unknown_tool_is_rejected_without_execution(self):
        model = ScriptedModel(
            [
                tool("bad", "delete_everything", {}),
                ModelReply("该工具不可用。"),
            ]
        )
        manager = ConversationManager(
            ConversationAgent(model, lambda _: "should-not-run")
        )
        session = manager.create()

        result = manager.send(session.session_id, "删除数据")

        self.assertEqual(result.trace[0].status, "failed")
        self.assertIn("白名单", result.trace[0].summary)
        self.assertIsNone(result.job_id)

    def test_duplicate_submit_is_idempotent(self):
        complete = {
            "start_city": "甲市",
            "start_name": "起点",
            "end_city": "乙市",
            "end_name": "终点",
            "lunch_standard_per_person": 50,
            "dinner_standard_per_person": 80,
            "lodging_standard_per_person": 250,
        }
        model = ScriptedModel(
            [
                tool("update", "update_trip_request", complete),
                tool("submit-1", "submit_trip_plan", {}),
                ModelReply("已提交。"),
                tool("submit-2", "submit_trip_plan", {}),
                ModelReply("任务已在运行。"),
            ]
        )
        submissions = []
        manager = ConversationManager(
            ConversationAgent(
                model,
                lambda submission: submissions.append(submission) or "job-one",
            )
        )
        session = manager.create()

        manager.send(session.session_id, "全部参数")
        result = manager.send(session.session_id, "再提交一次")

        self.assertEqual(result.job_id, "job-one")
        self.assertEqual(len(submissions), 1)
        self.assertIn("未重复提交", result.trace[0].summary)

    def test_ollama_adapter_converts_tool_call_id_to_tool_name(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "update_trip_request",
                            "arguments": '{"start_city":"甲市"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"status":"updated"}',
            },
        ]

        converted = OllamaFunctionModel._ollama_messages(messages)

        self.assertEqual(
            converted[0]["tool_calls"][0]["function"]["arguments"],
            {"start_city": "甲市"},
        )
        self.assertEqual(converted[1]["tool_name"], "update_trip_request")
        self.assertNotIn("tool_call_id", converted[1])


if __name__ == "__main__":
    unittest.main()
