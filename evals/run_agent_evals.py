"""用少量真实 LLM 请求评测多轮对话和 Function Calling，不调用高德。"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from cycling_agent.conversation_agent import (
    ConversationAgent,
    ConversationManager,
    create_function_calling_model,
)


ROOT = Path(__file__).resolve().parents[1]


def normalize(value):
    return value.strip() if isinstance(value, str) else value


def run(provider: str | None = None) -> bool:
    load_dotenv(ROOT / ".env")
    cases = json.loads((Path(__file__).with_name("cases.json")).read_text("utf-8"))
    model = create_function_calling_model(provider)
    passed_cases = 0
    total_fields = 0
    correct_fields = 0

    print(f"Agent 真实模型评测：{model.model}")
    print("注：只评测对话与工具选择，不消耗高德额度。\n")

    for index, case in enumerate(cases, start=1):
        submitted = []
        manager = ConversationManager(
            ConversationAgent(
                model,
                lambda submission, i=index: submitted.append(submission) or f"eval-{i}",
            )
        )
        session = manager.create()
        last_result = None
        for turn in case["turns"]:
            last_result = manager.send(session.session_id, turn)

        field_results = []
        for name, expected in case["expected_fields"].items():
            actual = session.fields.get(name)
            correct = normalize(actual) == normalize(expected)
            total_fields += 1
            correct_fields += int(correct)
            field_results.append(correct)

        submitted_ok = bool(submitted) == case["expected_submitted"]
        case_passed = all(field_results) and submitted_ok
        passed_cases += int(case_passed)
        mark = "PASS" if case_passed else "FAIL"
        print(f"[{mark}] {case['name']}")
        if not case_passed:
            print("  期望字段：", case["expected_fields"])
            print("  实际字段：", session.fields)
            print("  是否提交：", bool(submitted))
            if last_result:
                print("  最后回复：", last_result.reply)

    field_accuracy = correct_fields / total_fields if total_fields else 0
    completion_rate = passed_cases / len(cases) if cases else 0
    print(f"\n字段准确率：{field_accuracy:.1%}")
    print(f"任务完成率：{completion_rate:.1%}")
    return passed_cases == len(cases)


def main():
    parser = argparse.ArgumentParser(description="骑行 Agent 真实模型评测")
    parser.add_argument(
        "--provider",
        choices=("deepseek", "openai", "ollama"),
        help="覆盖 .env 中的 LLM_PROVIDER",
    )
    args = parser.parse_args()
    raise SystemExit(0 if run(args.provider) else 1)


if __name__ == "__main__":
    main()
