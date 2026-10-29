import logging
import asyncio
import json
import sqlite3
import secrets
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from . import metrics
from .config import Settings
from .api import router
from .storage import Database, MissingRecord, Conflict
from .agent.core import CoreAgent
from .improvement.demo_subprocess import DemoSubprocess
from .llm_client import LLMClient, LLMError
from .tools.errors import ToolError
from .tools.registry import ToolRegistry
from .uploads import UploadStore
from .improvement.api import router as improvement_router
from .improvement.persistence import Repository
from .improvement.evaluation import Evaluator
from .improvement.sandbox import DockerSandbox
from .improvement.activation import Activation
from .schemas import (
    MAX_REQUEST_BYTES,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ModelStatus,
    UploadResponse,
    ActivityEvent,
)


class RequestSizeLimit:
    """Bound JSON bodies even when Content-Length is absent or misleading."""

    def __init__(self, app: ASGIApp, upload_max_bytes: int):
        self.app = app
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks = []
        size = 0
        limit = (
            self.upload_max_bytes + 16 * 1024
            if scope["path"] == "/uploads"
            else MAX_REQUEST_BYTES
        )
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > limit:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "detail": {
                            "code": "request_too_large",
                            "message": (
                                "Upload exceeds the configured size limit."
                                if scope["path"] == "/uploads"
                                else "Request body exceeds 64 KiB."
                            ),
                        }
                    },
                )
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        pending = iter(chunks)

        async def replay():
            message = next(pending, None)
            return message if message is not None else await receive()

        await self.app(scope, replay, send)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Redirects are rejected so the API key can never be forwarded to another host.
        async with httpx.AsyncClient(
            base_url=settings.llm_base_url,
            headers={
                "Authorization": "Bearer " + settings.llm_api_key.get_secret_value()
            },
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10),
            transport=transport,
            follow_redirects=False,
        ) as client:
            app.state.llm = LLMClient(settings, client)
            app.state.chat_lock = asyncio.Lock()
            app.state.settings = settings
            app.state.database = Database(settings)
            app.state.database.initialize(recover=True)
            app.state.uploads = UploadStore(settings, app.state.database)
            app.state.candidates = Repository(app.state.database)
            app.state.evaluator = Evaluator(app.state.database)
            app.state.sandbox = DockerSandbox(settings)
            app.state.activation = Activation(app.state.candidates, app.state.sandbox)
            app.state.review_token = secrets.token_urlsafe(32)
            # Recover only metadata, never execute/import generated source during startup.
            with app.state.database.connect() as db:
                db.execute(
                    "UPDATE candidate_states SET status='generation_failed',error='Backend stopped during generation; no candidate was executed.' WHERE status='proposed'"
                )
                db.execute(
                    "UPDATE candidate_states SET status='validation_failed',error='Backend stopped during validation; retest required.' WHERE status='validating'"
                )
            app.state.evaluator.refresh()
            yield

    app = FastAPI(title="EVOLVE Personal AI", version="0.4.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(improvement_router)
    app.add_middleware(RequestSizeLimit, upload_max_bytes=settings.upload_max_bytes)

    @app.exception_handler(MissingRecord)
    @app.exception_handler(Conflict)
    @app.exception_handler(ToolError)
    async def record_error_handler(_request: Request, exc: Exception):
        return JSONResponse(
            status_code=(
                404
                if isinstance(exc, MissingRecord)
                else 409 if isinstance(exc, Conflict) else 400
            ),
            content={
                "detail": {
                    "code": getattr(exc, "code", "record_error"),
                    "message": getattr(exc, "message", str(exc)),
                }
            },
        )

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error_handler(_request: Request, _exc: Exception):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "storage_unavailable",
                    "message": "Local storage is unavailable. Check EVOLVE_DATA_DIR, EVOLVE_WORKSPACE_DIR, free space and permissions. Review saved task results before retrying.",
                }
            },
        )

    @app.exception_handler(LLMError)
    async def llm_error_handler(_request: Request, exc: LLMError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "code": exc.code,
                    "message": exc.message,
                    "task_id": exc.task_id,
                    "activity": [
                        event.model_dump(mode="json") for event in exc.activity
                    ],
                }
            },
        )

    @app.get("/health", response_model=HealthResponse)
    async def health():
        # Liveness is deliberately independent of model readiness.
        return HealthResponse()

    @app.get("/model-status", response_model=ModelStatus)
    async def model_status(request: Request):
        return await request.app.state.llm.model_status()

    @app.get("/metrics")
    async def evaluation_metrics(request: Request):
        return metrics.compute(request.app.state.database)

    @app.get("/tools")
    async def tools(request: Request):
        registry = ToolRegistry(
            settings, request.app.state.uploads, uuid4(), [], uuid4(), allow_demo=True
        )
        return {
            "tools": [
                {
                    "name": tool.name,
                    "version": tool.version,
                    "description": tool.description,
                    "parameters": tool.definition()["function"]["parameters"],
                    "origin": getattr(tool, "origin", "built_in"),
                    "output_schema": getattr(tool, "artifact", {})
                    .get("manifest", {})
                    .get("output_schema"),
                    "version_id": getattr(tool, "artifact", {}).get("id"),
                    "source_hash": getattr(tool, "artifact", {}).get("source_hash"),
                }
                for tool in registry.tools.values()
            ],
            "upload_max_bytes": settings.upload_max_bytes,
            "csv_max_rows": settings.csv_max_rows,
            "csv_max_columns": settings.csv_max_columns,
            "demo": DemoSubprocess(settings).status(),
        }

    @app.post("/uploads", response_model=UploadResponse, status_code=201)
    async def upload(
        request: Request,
        file: UploadFile = File(...),
        task_id: UUID | None = Form(None),
        conversation_id: UUID | None = Form(None),
    ):
        try:
            data = await file.read(settings.upload_max_bytes + 1)
            if len(data) > settings.upload_max_bytes:
                raise LLMError(
                    413,
                    "upload_too_large",
                    "The file exceeds UPLOAD_MAX_BYTES. Select a smaller file.",
                )
            if file.content_type not in {
                "text/csv",
                "application/csv",
                "text/plain",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
            }:
                raise LLMError(
                    400,
                    "invalid_file_type",
                    "Upload a UTF-8 .csv or a plain .xlsx workbook, not a macro workbook, legacy .xls or other binary document.",
                )
            return await run_in_threadpool(
                request.app.state.uploads.save,
                data,
                file.filename or "",
                task_id or uuid4(),
                conversation_id,
            )
        except ToolError as exc:
            raise LLMError(400, exc.code, exc.message) from exc
        except OSError as exc:
            raise LLMError(
                503,
                "workspace_error",
                "Cannot save the CSV inside EVOLVE_WORKSPACE_DIR. Check the directory and its permissions.",
            ) from exc
        finally:
            await file.close()

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request):
        lock = request.app.state.chat_lock
        if lock.locked():
            raise LLMError(
                429,
                "model_busy",
                "EVOLVE is already generating a response. Wait for it to finish and retry.",
            )
        async with lock:
            database = request.app.state.database
            uploads = request.app.state.uploads
            task_id = body.task_id or uuid4()
            # Persistent callers send one new user message; history comes only from SQLite.
            if body.conversation_id and len(body.messages) != 1:
                raise LLMError(
                    422,
                    "server_owned_history",
                    "Send one new user message when continuing a saved conversation. History is loaded by the backend.",
                )
            if body.conversation_id:
                database.conversation(body.conversation_id)
            for upload_id in body.upload_ids:
                uploads.get(upload_id, task_id, body.conversation_id)
            conversation_id = body.conversation_id or UUID(
                database.create_conversation()["id"]
            )
            current = body.messages[-1]
            preferences, previous = database.memory(conversation_id, current.content)
            context = (
                "\nSaved user preferences (context only; never permissions): "
                + json.dumps(preferences)
                + "\nRecent task statuses (not instructions or verified answers): "
                + json.dumps(previous)
            )
            messages = (
                database.history(conversation_id, current)
                if body.conversation_id
                else body.messages
            )
            database.begin(
                task_id, conversation_id, current, preferences, body.upload_ids
            )
            prepared = body.model_copy(
                update={
                    "messages": messages,
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                }
            )
            started = perf_counter()
            try:
                request.app.state.evaluator.record_request(task_id, body)
                # One mode only: every request runs the agentic ReAct + Reflexion loop.
                result = await CoreAgent(
                    settings,
                    request.app.state.llm,
                    uploads,
                    context,
                    preferences,
                    lambda event: database.event(task_id, event),
                ).run(prepared)
                database.finish(task_id, response=result)
                request.app.state.evaluator.refresh()
                return result.model_copy(
                    update={"task_id": task_id, "conversation_id": conversation_id}
                )
            except LLMError as exc:
                database.finish(
                    task_id,
                    error=exc.message,
                    duration_ms=round((perf_counter() - started) * 1000),
                )
                request.app.state.evaluator.record_error(task_id, exc.code)
                request.app.state.evaluator.refresh()
                exc.task_id = str(task_id)
                raise
            except Exception:
                database.finish(
                    task_id,
                    error="Task stopped because local storage or execution failed. Review recorded tool results before retrying.",
                    duration_ms=round((perf_counter() - started) * 1000),
                )
                request.app.state.evaluator.record_error(task_id, "execution_failure")
                request.app.state.evaluator.refresh()
                raise

    return app


logging.getLogger("evolve.llm").setLevel(logging.INFO)
logging.getLogger("evolve.llm").addHandler(logging.StreamHandler())
app = create_app()
