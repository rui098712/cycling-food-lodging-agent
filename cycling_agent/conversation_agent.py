"""多轮对话 + Function Calling Agent。"""

from dataclasses import dataclass, field
import json
import os
from threading import Lock
from typing import Any, Callable, Protocol
from uuid import uuid4

from ollama import Client as OllamaClient, ResponseError
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cycling_agent.llm_parser import LLMParsingError
from cycling_agent.models import TripRequest
from cycling_agent.web_service import PlanSubmission


SYSTEM_PROMPT = """
你是骑行食宿规划 Agent 的对话决策器。你的任务是通过多轮对话收集参数，然后调用规划工具。

必须遵守：
1. 用户提供或修改地点、城市、人均标准时，调用 update_trip_request 存入结构化状态。
2. 三个消费数字都是人民币元/人：人均午饭、人均晚饭、人均住宿。
3. 信息不全时，只询问缺失信息，不要猜测地名或价格。
4. 参数全部收集后，调用 submit_trip_plan。只有工具返回 submitted 才能说已开始规划。
5. 你不计算路线、距离、价格或 POI；这些由高德和确定性工具完成。
6. 回复简洁、中文友好，不暴露隐藏思维过程。
""".strip()


FIELD_LABELS = {
    "start_city": "起点城市或县",
    "start_name": "骑行起点",
    "end_city": "终点城市或县",
    "end_name": "骑行终点",
    "lunch_standard_per_person": "人均午饭标准",
    "dinner_standard_per_person": "人均晚饭标准",
    "lodging_standard_per_person": "人均住宿标准",
}


class TripRequestPatch(BaseModel):
    """模型一次对话中可以新增或修改的字段。"""

    model_config = ConfigDict(extra="forbid")

    start_city: str | None = None
    start_name: str | None = None
    end_city: str | None = None
    end_name: str | None = None
    lunch_standard_per_person: float | None = Field(default=None, gt=0)
    dinner_standard_per_person: float | None = Field(default=None, gt=0)
    lodging_standard_per_person: float | None = Field(default=None, gt=0)

    @field_validator("start_city", "start_name", "end_city", "end_name")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("地点字段不能是空字符串")
        if len(value) > 100:
            raise ValueError("地点字段过长")
        return value


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: str | dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    content: str | None
    tool_calls: tuple[ModelToolCall, ...] = ()


class FunctionCallingModel(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply: ...


class OpenAICompatibleFunctionModel:
    """DeepSeek/OpenAI 共用的 Chat Completions Function Calling 适配器。"""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        provider_name: str,
        request_options: dict[str, Any] | None = None,
    ):
        self.client = client
        self.model = model
        self.provider_name = provider_name
        self.request_options = request_options or {}

    def complete(self, messages, tools) -> ModelReply:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
                max_tokens=800,
                **self.request_options,
            )
        except OpenAIError as error:
            status_code = getattr(error, "status_code", None)
            hints = {
                401: "API Key 无效或已失效",
                402: "账户余额不足",
                429: "请求过于频繁，请稍后重试",
            }
            raise LLMParsingError(
                f"{self.provider_name} 对话请求失败：{hints.get(status_code) or error}"
            ) from error

        message = response.choices[0].message
        calls = tuple(
            ModelToolCall(
                call_id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in (message.tool_calls or ())
        )
        return ModelReply(content=message.content, tool_calls=calls)


class OllamaFunctionModel:
    """本地 Ollama 的工具调用适配器。"""

    def __init__(self, client: Any | None = None, model: str | None = None):
        self.client = client or OllamaClient(
            host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            trust_env=False,
        )
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:4b")

    def complete(self, messages, tools) -> ModelReply:
        try:
            response = self.client.chat(
                model=self.model,
                messages=self._ollama_messages(messages),
                tools=tools,
                think=False,
                options={"temperature": 0},
            )
        except (ConnectionError, ResponseError) as error:
            raise LLMParsingError(f"Ollama 对话请求失败：{error}") from error

        message = response.message
        calls = []
        for index, call in enumerate(message.tool_calls or ()):
            function = call.function
            calls.append(
                ModelToolCall(
                    call_id=getattr(call, "id", None) or f"ollama_call_{index}",
                    name=function.name,
                    arguments=function.arguments,
                )
            )
        return ModelReply(content=message.content or None, tool_calls=tuple(calls))

    @staticmethod
    def _ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI 用 call id 配对结果，Ollama 用 tool_name，在适配层转换。"""

        call_names: dict[str, str] = {}
        converted = []
        for message in messages:
            item = dict(message)
            if item.get("role") == "assistant" and item.get("tool_calls"):
                normalized_calls = []
                for call in item["tool_calls"]:
                    function = dict(call["function"])
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    function["arguments"] = arguments
                    normalized_calls.append({"function": function})
                    if call.get("id"):
                        call_names[call["id"]] = function["name"]
                item["tool_calls"] = normalized_calls
            elif item.get("role") == "tool":
                call_id = item.pop("tool_call_id", None)
                item["tool_name"] = call_names.get(call_id, "unknown_tool")
            converted.append(item)
        return converted


def create_function_calling_model(provider: str | None = None) -> FunctionCallingModel:
    provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).strip().lower()
    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("没有找到 DEEPSEEK_API_KEY")
        return OpenAICompatibleFunctionModel(
            client=OpenAI(
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            ),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            provider_name="DeepSeek",
            request_options={"extra_body": {"thinking": {"type": "disabled"}}},
        )
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("没有找到 OPENAI_API_KEY")
        return OpenAICompatibleFunctionModel(
            client=OpenAI(),
            model=os.getenv("OPENAI_MODEL", "gpt-6-astra"),
            provider_name="OpenAI",
        )
    if provider == "ollama":
        return OllamaFunctionModel()
    raise ValueError("LLM_PROVIDER 只能是 ollama、deepseek 或 openai")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "update_trip_request",
            "description": "保存用户明确提供的行程字段；可以只传本轮新增或修改的字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_city": {"type": "string", "description": "起点市或县"},
                    "start_name": {"type": "string", "description": "起点地名"},
                    "end_city": {"type": "string", "description": "终点市或县"},
                    "end_name": {"type": "string", "description": "终点地名"},
                    "lunch_standard_per_person": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "人均午饭标准，元/人",
                    },
                    "dinner_standard_per_person": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "人均晚饭标准，元/人",
                    },
                    "lodging_standard_per_person": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "人均住宿标准，元/人",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_trip_plan",
            "description": "只有当七个行程字段都已收集时，提交后台高德骑行规划任务。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


@dataclass(frozen=True)
class ConversationToolEvent:
    step: int
    tool_name: str
    status: str
    summary: str


@dataclass
class ConversationSession:
    session_id: str
    messages: list[dict[str, Any]] = field(
        default_factory=lambda: [{"role": "system", "content": SYSTEM_PROMPT}]
    )
    fields: dict[str, Any] = field(
        default_factory=lambda: {name: None for name in FIELD_LABELS}
    )
    job_id: str | None = None
    status_url: str | None = None
    trace: list[ConversationToolEvent] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock, repr=False)

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name, value in self.fields.items() if value is None)


@dataclass(frozen=True)
class ConversationTurnResult:
    session_id: str
    reply: str
    status: str
    fields: dict[str, Any]
    missing_fields: tuple[str, ...]
    job_id: str | None
    status_url: str | None
    trace: tuple[ConversationToolEvent, ...]


class ConversationAgent:
    """LLM 决策、本地执行工具的受控 Agent 循环。"""

    def __init__(
        self,
        model: FunctionCallingModel,
        submit_plan: Callable[[PlanSubmission], str],
        *,
        max_steps: int = 6,
        max_history_messages: int = 40,
    ):
        self.model = model
        self.submit_plan = submit_plan
        self.max_steps = max_steps
        self.max_history_messages = max_history_messages

    def respond(self, session: ConversationSession, user_text: str) -> ConversationTurnResult:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("消息不能为空")
        if len(user_text) > 1000:
            raise ValueError("单条消息不能超过 1000 个字符")

        with session.lock:
            session.messages.append({"role": "user", "content": user_text})
            turn_trace: list[ConversationToolEvent] = []

            for step in range(1, self.max_steps + 1):
                reply = self.model.complete(self._model_messages(session), TOOLS)
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": reply.content,
                }
                if reply.tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": (
                                    call.arguments
                                    if isinstance(call.arguments, str)
                                    else json.dumps(call.arguments, ensure_ascii=False)
                                ),
                            },
                        }
                        for call in reply.tool_calls
                    ]
                session.messages.append(assistant_message)

                if not reply.tool_calls:
                    content = (reply.content or "").strip()
                    if not content:
                        content = self._fallback_reply(session)
                    self._trim_history(session)
                    return self._result(session, content, tuple(turn_trace))

                for call in reply.tool_calls:
                    event, output = self._execute_tool(session, step, call)
                    turn_trace.append(event)
                    session.trace.append(event)
                    session.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": json.dumps(output, ensure_ascii=False),
                        }
                    )

            raise RuntimeError(f"Agent 超过单轮最大工具步数 {self.max_steps}")

    def _execute_tool(
        self,
        session: ConversationSession,
        step: int,
        call: ModelToolCall,
    ) -> tuple[ConversationToolEvent, dict[str, Any]]:
        try:
            arguments = (
                json.loads(call.arguments)
                if isinstance(call.arguments, str)
                else call.arguments
            )
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须是 JSON 对象")

            if call.name == "update_trip_request":
                patch = TripRequestPatch.model_validate(arguments)
                changed = []
                for name in patch.model_fields_set:
                    value = getattr(patch, name)
                    if value is not None:
                        if session.fields[name] == value:
                            continue
                        session.fields[name] = value
                        changed.append(FIELD_LABELS[name])
                if changed and session.job_id is not None:
                    # 用户修改了需求，下次允许以新参数提交新任务。
                    session.job_id = None
                    session.status_url = None
                missing = session.missing_fields()
                output = {
                    "status": "updated",
                    "updated_fields": changed,
                    "missing_fields": [FIELD_LABELS[name] for name in missing],
                    "ready_to_submit": not missing,
                }
                summary = "已更新" + ("、".join(changed) if changed else "无字段")
            elif call.name == "submit_trip_plan":
                if arguments:
                    raise ValueError("submit_trip_plan 不接受参数")
                missing = session.missing_fields()
                if missing:
                    output = {
                        "status": "rejected",
                        "reason": "missing_fields",
                        "missing_fields": [FIELD_LABELS[name] for name in missing],
                    }
                    summary = "提交被拒绝：信息尚未齐全"
                elif session.job_id is not None:
                    output = {
                        "status": "already_submitted",
                        "job_id": session.job_id,
                        "status_url": session.status_url,
                    }
                    summary = "已有规划任务，未重复提交"
                else:
                    request = TripRequest(**session.fields)
                    session.job_id = self.submit_plan(
                        PlanSubmission(
                            start_city=request.start_city,
                            start_name=request.start_name,
                            end_city=request.end_city,
                            end_name=request.end_name,
                            lunch_standard_per_person=request.lunch_standard_per_person,
                            dinner_standard_per_person=request.dinner_standard_per_person,
                            lodging_standard_per_person=request.lodging_standard_per_person,
                        )
                    )
                    session.status_url = f"/api/plans/{session.job_id}"
                    output = {
                        "status": "submitted",
                        "job_id": session.job_id,
                        "status_url": session.status_url,
                    }
                    summary = "已提交后台规划任务"
            else:
                raise ValueError(f"工具未在白名单中：{call.name}")

            return (
                ConversationToolEvent(step, call.name, "succeeded", summary),
                output,
            )
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
            output = {"status": "error", "message": str(error)}
            return (
                ConversationToolEvent(
                    step,
                    call.name,
                    "failed",
                    f"工具参数校验失败：{error}",
                ),
                output,
            )

    def _fallback_reply(self, session: ConversationSession) -> str:
        if session.job_id:
            return "信息已齐全，规划任务已开始。"
        missing = session.missing_fields()
        if missing:
            return "请继续补充：" + "、".join(FIELD_LABELS[name] for name in missing)
        return "行程信息已齐全，可以开始规划。"

    @staticmethod
    def _model_messages(session: ConversationSession) -> list[dict[str, Any]]:
        """每次请求都附带结构化状态，避免长对话裁剪后丢失关键参数。"""

        snapshot = {
            "collected_fields": session.fields,
            "missing_fields": [FIELD_LABELS[name] for name in session.missing_fields()],
            "submitted_job_id": session.job_id,
        }
        return [
            *session.messages,
            {
                "role": "system",
                "content": "当前工具状态（以此为准）："
                + json.dumps(snapshot, ensure_ascii=False),
            },
        ]

    def _trim_history(self, session: ConversationSession) -> None:
        if len(session.messages) <= self.max_history_messages:
            return
        system = session.messages[0]
        recent = session.messages[-(self.max_history_messages - 1) :]
        # 不从 tool 消息开始，避免破坏 tool_call/tool 的配对。
        while recent and recent[0].get("role") == "tool":
            recent.pop(0)
        session.messages = [system, *recent]

    @staticmethod
    def _result(
        session: ConversationSession,
        reply: str,
        trace: tuple[ConversationToolEvent, ...],
    ) -> ConversationTurnResult:
        status = "submitted" if session.job_id else "collecting"
        return ConversationTurnResult(
            session_id=session.session_id,
            reply=reply,
            status=status,
            fields=dict(session.fields),
            missing_fields=session.missing_fields(),
            job_id=session.job_id,
            status_url=session.status_url,
            trace=trace,
        )


class ConversationManager:
    """内存会话管理；重启服务后会话消失，但不影响已提交规划。"""

    def __init__(self, agent: ConversationAgent):
        self.agent = agent
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = Lock()

    def create(self) -> ConversationSession:
        session = ConversationSession(session_id=uuid4().hex)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ConversationSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def send(self, session_id: str, message: str) -> ConversationTurnResult:
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return self.agent.respond(session, message)


def create_conversation_manager(
    submit_plan: Callable[[PlanSubmission], str],
) -> ConversationManager:
    return ConversationManager(
        ConversationAgent(create_function_calling_model(), submit_plan)
    )
