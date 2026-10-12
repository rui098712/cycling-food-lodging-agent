"""Background planning jobs and JSON conversion for the mobile web app."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
import os
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from cycling_agent.agent import CyclingPlannerAgent
from cycling_agent.agent_runtime import (
    AgentTraceEvent,
    PlanValidationReport,
    PlanningAgentRuntime,
)
from cycling_agent.amap_client import AmapClient
from cycling_agent.gpx_io import import_gpx
from cycling_agent.models import Poi, Recommendation, TripPlan, TripRequest
from cycling_agent.parser_factory import create_parser


FIELD_LABELS = {
    "start_city": "起点城市或县",
    "start_name": "骑行起点",
    "end_city": "终点城市或县",
    "end_name": "骑行终点",
    "lunch_standard_per_person": "人均午饭餐标",
    "dinner_standard_per_person": "人均晚饭餐标",
    "lodging_standard_per_person": "人均住宿标准",
}


@dataclass(frozen=True)
class PlanSubmission:
    description: str | None = None
    start_city: str | None = None
    start_name: str | None = None
    end_city: str | None = None
    end_name: str | None = None
    lunch_standard_per_person: float | None = None
    dinner_standard_per_person: float | None = None
    lodging_standard_per_person: float | None = None
    gpx_bytes: bytes | None = None
    gpx_filename: str | None = None


@dataclass
class PlanJob:
    job_id: str
    status: str = "queued"
    stage: str = "已提交，等待开始"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    result: dict[str, Any] | None = None
    error: str | None = None
    output_gpx: Path | None = None
    agent_trace: list[dict[str, Any]] = field(default_factory=list)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _poi_json(poi: Poi) -> dict[str, Any]:
    return {
        "name": poi.name,
        "address": poi.address or "暂无",
        "longitude": poi.location[0],
        "latitude": poi.location[1],
        "type_name": poi.type_name,
        "rating": poi.rating,
        "cost": poi.cost,
        "distance_meters": poi.distance_meters,
        "route_ratio": poi.route_ratio,
        "city_name": poi.city_name,
        "district_name": poi.district_name,
        "township_name": poi.township_name,
        "lunch_area_kind": poi.lunch_area_kind,
    }


def _recommendation_json(item: Recommendation) -> dict[str, Any]:
    return {
        "poi": _poi_json(item.poi),
        "score": round(item.score, 1),
        "reasons": list(item.reasons),
    }


def _agent_event_json(event: AgentTraceEvent) -> dict[str, Any]:
    return {
        "step": event.step,
        "event_type": event.event_type,
        "name": event.name,
        "status": event.status,
        "summary": event.summary,
    }


def _validation_json(report: PlanValidationReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
            }
            for check in report.checks
        ],
    }


def trip_plan_to_json(plan: TripPlan) -> dict[str, Any]:
    """Expose only stable, display-oriented fields to the browser."""

    rest_stops = []
    for stop in plan.rest_stops:
        rest_stops.append(
            {
                "sequence": stop.sequence,
                "search_start_meters": stop.search_start_meters,
                "search_end_meters": stop.search_end_meters,
                "search_mode": stop.search_mode,
                "category": stop.category,
                "route_progress_meters": stop.route_progress_meters,
                "distance_from_route_meters": stop.distance_from_route_meters,
                "distance_since_previous_meters": stop.distance_since_previous_meters,
                "poi": _poi_json(stop.poi) if stop.poi else None,
            }
        )

    lodging_options = []
    for option in plan.lodging_options:
        lodging_options.append(
            {
                "lodging": _recommendation_json(option.lodging),
                "dinner": (
                    _recommendation_json(option.dinner) if option.dinner else None
                ),
                "dinner_road_distance_meters": (
                    option.dinner_road_distance_meters
                ),
            }
        )

    def ordered_unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    lunch_search_areas = {
        "cities": ordered_unique(
            [area.city_name for area in plan.lunch_areas]
        ),
        "counties_and_districts": ordered_unique(
            [area.district_name for area in plan.lunch_areas]
        ),
        "townships_and_streets": ordered_unique(
            [area.township_name for area in plan.lunch_areas]
        ),
    }

    return {
        "route": {
            "source": plan.route.source,
            "source_label": (
                "导入的 GPX 固定轨迹"
                if plan.route.source == "gpx"
                else "高德骑行路径规划"
            ),
            "start_address": plan.start.formatted_address,
            "end_address": plan.end.formatted_address,
            "distance_meters": plan.route.distance_meters,
            "duration_seconds": plan.route.duration_seconds,
            "point_count": plan.route.point_count,
            "lunch_search_point_count": plan.lunch_search_point_count,
        },
        "lunch_search_areas": lunch_search_areas,
        "lunch_recommendations": [
            _recommendation_json(item) for item in plan.lunch_recommendations
        ],
        "rest_stops": rest_stops,
        "lodging_options": lodging_options,
        "warnings": list(plan.warnings),
    }


class PlanningJobManager:
    """Run slow map work away from HTTP request threads."""

    def __init__(
        self,
        storage_dir: str | Path = ".runtime/plans",
        *,
        planner_factory: Callable[[], CyclingPlannerAgent] | None = None,
        parser_factory: Callable[[], tuple[str, Any]] | None = None,
        max_workers: int = 2,
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.planner_factory = planner_factory or self._default_planner_factory
        self.parser_factory = parser_factory or create_parser
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="web-plan",
        )
        self._jobs: dict[str, PlanJob] = {}
        self._lock = Lock()

    @staticmethod
    def _default_planner_factory() -> CyclingPlannerAgent:
        api_key = os.getenv("AMAP_API_KEY")
        if not api_key:
            raise ValueError("没有找到 AMAP_API_KEY，请检查 .env 文件")
        return CyclingPlannerAgent(AmapClient(api_key))

    def submit(self, submission: PlanSubmission) -> str:
        job_id = uuid4().hex
        with self._lock:
            self._jobs[job_id] = PlanJob(job_id=job_id)
        self._executor.submit(self._run, job_id, submission)
        return job_id

    def get(self, job_id: str) -> PlanJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def public_status(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        payload: dict[str, Any] = {
            "job_id": job.job_id,
            "status": job.status,
            "stage": job.stage,
            "created_at": job.created_at,
            "agent_trace": list(job.agent_trace),
        }
        if job.result is not None:
            payload["result"] = job.result
            payload["gpx_download_url"] = f"/api/plans/{job_id}/gpx"
        if job.error:
            payload["error"] = job.error
        return payload

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _record_agent_event(self, job_id: str, event: AgentTraceEvent) -> None:
        """同步保存轨迹，让网页轮询时能看到 Agent 正在做什么。"""

        with self._lock:
            job = self._jobs[job_id]
            job.agent_trace.append(_agent_event_json(event))
            job.stage = event.summary

    def _build_request(
        self,
        submission: PlanSubmission,
        *,
        gpx_route_name: str | None,
    ) -> TripRequest:
        explicit_values = {
            "start_city": _clean_text(submission.start_city),
            "start_name": _clean_text(submission.start_name),
            "end_city": _clean_text(submission.end_city),
            "end_name": _clean_text(submission.end_name),
            "lunch_standard_per_person": submission.lunch_standard_per_person,
            "dinner_standard_per_person": submission.dinner_standard_per_person,
            "lodging_standard_per_person": submission.lodging_standard_per_person,
        }

        values: dict[str, Any] = {}
        description = _clean_text(submission.description)
        if description:
            _, parser = self.parser_factory()
            values.update(parser.parse(description).model_dump())
        values.update(
            {key: value for key, value in explicit_values.items() if value is not None}
        )

        if gpx_route_name:
            defaults = {
                "start_city": "GPX",
                "start_name": f"{gpx_route_name}轨迹起点",
                "end_city": "GPX",
                "end_name": f"{gpx_route_name}轨迹终点",
            }
            for key, value in defaults.items():
                values.setdefault(key, value)

        missing = [key for key in FIELD_LABELS if values.get(key) is None]
        if missing:
            labels = "、".join(FIELD_LABELS[key] for key in missing)
            raise ValueError(f"请补充：{labels}")

        for key in (
            "lunch_standard_per_person",
            "dinner_standard_per_person",
            "lodging_standard_per_person",
        ):
            if float(values[key]) <= 0:
                raise ValueError(f"{FIELD_LABELS[key]}必须大于 0")
            values[key] = float(values[key])
        return TripRequest(**values)

    def _run(self, job_id: str, submission: PlanSubmission) -> None:
        job_directory = self.storage_dir / job_id
        try:
            self._update(job_id, status="running", stage="正在准备路线")
            job_directory.mkdir(parents=True, exist_ok=False)

            imported_gpx = None
            source_path = None
            if submission.gpx_bytes is not None:
                source_path = job_directory / "source.gpx"
                source_path.write_bytes(submission.gpx_bytes)
                imported_gpx = import_gpx(source_path)

            request = self._build_request(
                submission,
                gpx_route_name=imported_gpx.name if imported_gpx else None,
            )
            self._update(
                job_id,
                stage=(
                    "正在按 GPX 轨迹搜索食宿与休息点"
                    if imported_gpx
                    else "正在调用高德规划道路并搜索推荐点"
                ),
            )

            output_path = job_directory / "cycling-plan.gpx"
            runtime = PlanningAgentRuntime(self.planner_factory())
            run = runtime.run(
                request,
                route=imported_gpx.route if imported_gpx else None,
                output_path=output_path,
                route_name=(
                    f"{imported_gpx.name}（含食宿休息点）"
                    if imported_gpx
                    else "骑行食宿规划路线"
                ),
                segments_wgs84=(
                    imported_gpx.segments_wgs84 if imported_gpx else None
                ),
                source_path=source_path,
                on_event=lambda event: self._record_agent_event(job_id, event),
            )
            result = trip_plan_to_json(run.plan)
            result["agent"] = {
                "architecture": "plan-act-observe",
                "validation": _validation_json(run.validation),
                "trace": [_agent_event_json(event) for event in run.trace],
            }
            self._update(
                job_id,
                status="completed",
                stage="规划完成",
                result=result,
                output_gpx=run.exported_path,
            )
        except Exception as error:  # Worker failures must become a visible job state.
            self._update(
                job_id,
                status="failed",
                stage="规划失败",
                error=str(error) or type(error).__name__,
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
