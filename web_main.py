"""FastAPI entry point for phone and desktop browsers."""

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cycling_agent.conversation_agent import (
    ConversationManager,
    create_conversation_manager,
)
from cycling_agent.gpx_io import MAX_GPX_FILE_BYTES
from cycling_agent.llm_parser import LLMParsingError
from cycling_agent.web_service import PlanSubmission, PlanningJobManager


PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "web" / "static"
load_dotenv(PROJECT_DIR / ".env")


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def create_app(
    manager: PlanningJobManager | None = None,
    conversation_manager: ConversationManager | None = None,
) -> FastAPI:
    job_manager = manager or PlanningJobManager(PROJECT_DIR / ".runtime" / "plans")
    chat_manager = conversation_manager
    chat_manager_lock = Lock()

    def get_chat_manager() -> ConversationManager:
        nonlocal chat_manager
        if chat_manager is None:
            with chat_manager_lock:
                if chat_manager is None:
                    chat_manager = create_conversation_manager(job_manager.submit)
        return chat_manager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        job_manager.shutdown()

    app = FastAPI(
        title="骑行食宿规划 Agent",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.job_manager = job_manager
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/chat/sessions", status_code=status.HTTP_201_CREATED)
    def create_chat_session():
        try:
            session = get_chat_manager().create()
        except ValueError as error:
            raise HTTPException(503, f"Agent 模型未配置：{error}") from error
        return {
            "session_id": session.session_id,
            "reply": (
                "你好，请告诉我骑行起点、终点，以及人均午饭、"
                "晚饭和住宿标准。可以分多次说。"
            ),
        }

    @app.post("/api/chat/sessions/{session_id}/messages")
    def send_chat_message(session_id: str, body: ChatMessageRequest):
        try:
            result = get_chat_manager().send(session_id, body.message)
        except KeyError as error:
            raise HTTPException(404, "对话已失效，请新建会话") from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except LLMParsingError as error:
            raise HTTPException(502, str(error)) from error

        return {
            "session_id": result.session_id,
            "reply": result.reply,
            "status": result.status,
            "fields": result.fields,
            "missing_fields": list(result.missing_fields),
            "job_id": result.job_id,
            "status_url": result.status_url,
            "tool_trace": [
                {
                    "step": event.step,
                    "tool_name": event.tool_name,
                    "status": event.status,
                    "summary": event.summary,
                }
                for event in result.trace
            ],
        }

    @app.post("/api/plans", status_code=status.HTTP_202_ACCEPTED)
    async def create_plan(
        description: str | None = Form(default=None),
        start_city: str | None = Form(default=None),
        start_name: str | None = Form(default=None),
        end_city: str | None = Form(default=None),
        end_name: str | None = Form(default=None),
        lunch_standard_per_person: float | None = Form(default=None),
        dinner_standard_per_person: float | None = Form(default=None),
        lodging_standard_per_person: float | None = Form(default=None),
        gpx_file: UploadFile | None = File(default=None),
    ):
        gpx_bytes = None
        gpx_filename = None
        if gpx_file is not None:
            gpx_filename = gpx_file.filename or "route.gpx"
            if not gpx_filename.lower().endswith(".gpx"):
                raise HTTPException(400, "请上传 .gpx 格式的轨迹文件")
            gpx_bytes = await gpx_file.read(MAX_GPX_FILE_BYTES + 1)
            if len(gpx_bytes) > MAX_GPX_FILE_BYTES:
                raise HTTPException(413, "GPX 文件不能超过 20 MB")
            if not gpx_bytes:
                raise HTTPException(400, "上传的 GPX 文件为空")

        if not (description and description.strip()) and not gpx_bytes:
            required_locations = [start_city, start_name, end_city, end_name]
            if not all(value and value.strip() for value in required_locations):
                raise HTTPException(400, "请填写完整起终点，或上传 GPX 轨迹")

        job_id = job_manager.submit(
            PlanSubmission(
                description=description,
                start_city=start_city,
                start_name=start_name,
                end_city=end_city,
                end_name=end_name,
                lunch_standard_per_person=lunch_standard_per_person,
                dinner_standard_per_person=dinner_standard_per_person,
                lodging_standard_per_person=lodging_standard_per_person,
                gpx_bytes=gpx_bytes,
                gpx_filename=gpx_filename,
            )
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/api/plans/{job_id}",
        }

    @app.get("/api/plans/{job_id}")
    def get_plan(job_id: str):
        payload = job_manager.public_status(job_id)
        if payload is None:
            raise HTTPException(404, "找不到该规划任务")
        return payload

    @app.get("/api/plans/{job_id}/gpx")
    def download_gpx(job_id: str):
        job = job_manager.get(job_id)
        if job is None:
            raise HTTPException(404, "找不到该规划任务")
        if job.status != "completed" or job.output_gpx is None:
            raise HTTPException(409, "GPX 尚未生成")
        if not job.output_gpx.is_file():
            raise HTTPException(410, "GPX 文件已不存在")
        return FileResponse(
            job.output_gpx,
            media_type="application/gpx+xml",
            filename="cycling-plan.gpx",
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_main:app", host="0.0.0.0", port=8000, reload=False)
