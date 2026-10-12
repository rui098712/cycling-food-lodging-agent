"""状态驱动的 Agent 运行时。

这一层不让 LLM 猜测地图数据，而是根据当前状态选择下一个已注册工具，
并将每次决策、工具调用和观察结果记录为可展示的执行轨迹。
后续如果要尝试 LLM function calling，只需替换 policy，工具层无需重写。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.gpx_io import export_plan_gpx
from cycling_agent.models import BicycleRoute, Coordinate, TripPlan, TripRequest


@dataclass(frozen=True)
class AgentTraceEvent:
    """可向用户展示的 Agent 事件。

    summary 只记录简短决策依据，不保存模型隐藏思维过程。
    """

    step: int
    event_type: str
    name: str
    status: str
    summary: str


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PlanValidationReport:
    passed: bool
    checks: tuple[ValidationCheck, ...]


@dataclass
class AgentContext:
    request: TripRequest
    fixed_route: BicycleRoute | None = None
    plan: TripPlan | None = None
    validation: PlanValidationReport | None = None
    output_path: Path | None = None
    route_name: str = "骑行食宿规划路线"
    segments_wgs84: tuple[tuple[Coordinate, ...], ...] | None = None
    source_path: Path | None = None
    exported_path: Path | None = None


@dataclass(frozen=True)
class AgentAction:
    tool_name: str
    summary: str


@dataclass(frozen=True)
class AgentRunResult:
    plan: TripPlan
    validation: PlanValidationReport
    trace: tuple[AgentTraceEvent, ...]
    exported_path: Path | None = None


class AgentTool(Protocol):
    name: str
    description: str

    def run(self, context: AgentContext) -> str: ...


class ToolRegistry:
    """工具注册表：Agent 只能调用显式注册的工具。"""

    def __init__(self):
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ValueError(f"Agent 尝试调用未注册工具：{name}") from error

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)


class PlanTripTool:
    name = "plan_trip"
    description = "调用高德工具链或固定 GPX 路线，生成食宿和休息点方案"

    def __init__(self, planner: CyclingPlannerAgent):
        self.planner = planner

    def run(self, context: AgentContext) -> str:
        context.plan = (
            self.planner.plan_with_route(context.request, context.fixed_route)
            if context.fixed_route is not None
            else self.planner.plan(context.request)
        )
        route = context.plan.route
        return (
            f"获得 {route.distance_meters / 1000:.2f} 公里路线，"
            f"{len(context.plan.lunch_recommendations)} 个午饭候选，"
            f"{sum(stop.poi is not None for stop in context.plan.rest_stops)} 个休息点，"
            f"{len(context.plan.lodging_options)} 个住宿方案"
        )


class ValidatePlanTool:
    name = "validate_plan"
    description = "对路线、午饭区间、休息点偏离和晚饭道路距离做确定性校验"

    def run(self, context: AgentContext) -> str:
        if context.plan is None:
            raise ValueError("必须先生成方案，才能执行校验")

        plan = context.plan
        checks: list[ValidationCheck] = []
        checks.append(
            ValidationCheck(
                name="route_integrity",
                passed=plan.route.distance_meters > 0 and plan.route.point_count >= 2,
                detail=(
                    f"路线 {plan.route.distance_meters / 1000:.2f} 公里，"
                    f"包含 {plan.route.point_count} 个坐标点"
                ),
            )
        )

        lunch_outside = [
            item.poi.name
            for item in plan.lunch_recommendations
            if item.poi.route_ratio is not None
            and not 0.5 <= item.poi.route_ratio <= (2 / 3 + 0.001)
        ]
        checks.append(
            ValidationCheck(
                name="lunch_route_window",
                passed=not lunch_outside,
                detail=(
                    "所有午饭候选都位于路线 1/2～2/3 搜索区间"
                    if not lunch_outside
                    else f"区间外候选：{'、'.join(lunch_outside)}"
                ),
            )
        )

        invalid_stops = [
            stop.sequence
            for stop in plan.rest_stops
            if stop.poi is not None
            and (
                stop.distance_from_route_meters is None
                or stop.distance_from_route_meters > 300
                or stop.route_progress_meters is None
            )
        ]
        checks.append(
            ValidationCheck(
                name="rest_stop_constraints",
                passed=not invalid_stops,
                detail=(
                    "已找到的休息点均在原路线 300 米内"
                    if not invalid_stops
                    else "第 " + "、".join(map(str, invalid_stops))
                    + " 个休息点未通过偏离约束"
                ),
            )
        )

        invalid_dinners = [
            option.dinner.poi.name
            for option in plan.lodging_options
            if option.dinner is not None
            and (
                option.dinner_road_distance_meters is None
                or option.dinner_road_distance_meters > 3000
            )
        ]
        checks.append(
            ValidationCheck(
                name="dinner_road_distance",
                passed=not invalid_dinners,
                detail=(
                    "已匹配的晚饭点均通过 3 公里骑行道路距离验证"
                    if not invalid_dinners
                    else f"超距离约束：{'、'.join(invalid_dinners)}"
                ),
            )
        )

        context.validation = PlanValidationReport(
            passed=all(check.passed for check in checks),
            checks=tuple(checks),
        )
        passed_count = sum(check.passed for check in checks)
        return f"约束校验 {passed_count}/{len(checks)} 项通过"


class ExportGpxTool:
    name = "export_gpx"
    description = "将主路线和 Agent 推荐点导出为 GPX"

    def run(self, context: AgentContext) -> str:
        if context.plan is None or context.output_path is None:
            raise ValueError("导出 GPX 前缺少规划结果或输出路径")
        context.exported_path = export_plan_gpx(
            context.plan,
            context.output_path,
            route_name=context.route_name,
            segments_wgs84=context.segments_wgs84,
            source_path=context.source_path,
        )
        return f"已生成 GPX：{context.exported_path.name}"


class PlanningPolicy:
    """
    可控、可测试的默认决策器。

    它根据 AgentContext 选择下一个工具；后续可替换成输出同样
    AgentAction 的 LLM policy，但不把地图计算权交给 LLM。
    """

    def next_action(self, context: AgentContext) -> AgentAction | None:
        if context.plan is None:
            source = "用户导入的固定 GPX" if context.fixed_route else "高德骑行算路"
            return AgentAction("plan_trip", f"尚无方案，先使用{source}建立完整规划")
        if context.validation is None:
            return AgentAction("validate_plan", "方案已生成，需要用确定性规则复核关键约束")
        if context.output_path is not None and context.exported_path is None:
            return AgentAction("export_gpx", "约束校验完成，将路线与推荐点导出供导航使用")
        return None


class PlanningAgentRuntime:
    """Plan-Act-Observe 循环，带工具白名单和最大步数保护。"""

    def __init__(
        self,
        planner: CyclingPlannerAgent,
        *,
        policy: PlanningPolicy | None = None,
        max_steps: int = 6,
    ):
        if max_steps < 1:
            raise ValueError("max_steps 必须大于 0")
        self.max_steps = max_steps
        self.policy = policy or PlanningPolicy()
        self.tools = ToolRegistry()
        self.tools.register(PlanTripTool(planner))
        self.tools.register(ValidatePlanTool())
        self.tools.register(ExportGpxTool())

    def run(
        self,
        request: TripRequest,
        *,
        route: BicycleRoute | None = None,
        output_path: str | Path | None = None,
        route_name: str = "骑行食宿规划路线",
        segments_wgs84: tuple[tuple[Coordinate, ...], ...] | None = None,
        source_path: str | Path | None = None,
        on_event: Callable[[AgentTraceEvent], None] | None = None,
    ) -> AgentRunResult:
        context = AgentContext(
            request=request,
            fixed_route=route,
            output_path=Path(output_path) if output_path is not None else None,
            route_name=route_name,
            segments_wgs84=segments_wgs84,
            source_path=Path(source_path) if source_path is not None else None,
        )
        trace: list[AgentTraceEvent] = []

        def emit(event: AgentTraceEvent) -> None:
            trace.append(event)
            if on_event is not None:
                on_event(event)

        for step in range(1, self.max_steps + 1):
            action = self.policy.next_action(context)
            if action is None:
                if context.plan is None or context.validation is None:
                    raise RuntimeError("Agent 提前停止，但规划尚未完成")
                emit(
                    AgentTraceEvent(
                        step=step,
                        event_type="complete",
                        name="finish",
                        status="completed",
                        summary="规划、约束校验和所需输出均已完成",
                    )
                )
                return AgentRunResult(
                    plan=context.plan,
                    validation=context.validation,
                    trace=tuple(trace),
                    exported_path=context.exported_path,
                )

            emit(
                AgentTraceEvent(
                    step=step,
                    event_type="decision",
                    name=action.tool_name,
                    status="selected",
                    summary=action.summary,
                )
            )
            tool = self.tools.get(action.tool_name)
            try:
                observation = tool.run(context)
            except Exception as error:
                emit(
                    AgentTraceEvent(
                        step=step,
                        event_type="observation",
                        name=action.tool_name,
                        status="failed",
                        summary=f"工具执行失败：{str(error) or type(error).__name__}",
                    )
                )
                raise
            emit(
                AgentTraceEvent(
                    step=step,
                    event_type="observation",
                    name=action.tool_name,
                    status="succeeded",
                    summary=observation,
                )
            )

        raise RuntimeError(f"Agent 超过最大执行步数 {self.max_steps}，已安全停止")
