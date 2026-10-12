"""LLM 理解层与确定性地图规划层的组合入口。"""

from dataclasses import dataclass
from typing import Protocol

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.agent_runtime import (
    AgentTraceEvent,
    PlanValidationReport,
    PlanningAgentRuntime,
)
from cycling_agent.llm_parser import TripRequestDraft
from cycling_agent.models import BicycleRoute, TripPlan, TripRequest


@dataclass(frozen=True)
class LLMPlanningResult:
    """保留 LLM 理解结果和地图工具的最终结果，便于调试和评测。"""

    request: TripRequest
    plan: TripPlan
    validation: PlanValidationReport
    trace: tuple[AgentTraceEvent, ...]


class TripRequestParser(Protocol):
    """任意云端或本地模型解析器都要实现的最小接口。"""

    model: str

    def parse(self, user_text: str) -> TripRequestDraft: ...


class LLMCyclingAgent:
    """
    混合 Agent：LLM 负责语言理解，普通代码负责真实地图计算。

    parser 和 planner 通过构造函数注入，因此可以用假实现做离线测试。
    """

    def __init__(
        self,
        parser: TripRequestParser,
        planner: CyclingPlannerAgent,
    ):
        self.parser = parser
        self.planner = planner
        self.runtime = PlanningAgentRuntime(planner)

    def understand(self, user_text: str) -> TripRequestDraft:
        return self.parser.parse(user_text)

    def plan_request(
        self,
        request: TripRequest,
        route: BicycleRoute | None = None,
    ) -> LLMPlanningResult:
        run = self.runtime.run(request, route=route)
        return LLMPlanningResult(
            request=request,
            plan=run.plan,
            validation=run.validation,
            trace=run.trace,
        )

    def plan(self, user_text: str) -> LLMPlanningResult:
        draft = self.understand(user_text)
        return self.plan_request(draft.to_trip_request())
