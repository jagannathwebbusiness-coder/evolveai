"""Explicit human-facing endpoints; none are offered to the model as tools."""

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ..api import idle
from ..llm_client import LLMError
from ..schemas import ActivityEvent
from ..storage import Conflict, now
from .activation import Activation, authorize, review_origin
from .business_contracts import check_manifest
from .demo_subprocess import DemoSubprocess
from .general_builder import build_general
from .generation import generate
from .models import (
    Binding,
    CandidateDetail,
    ContractInput,
    DecisionInput,
    DraftToolInput,
    FeedbackInput,
    GenerateInput,
    RevisionInput,
    RunResponse,
    Snapshot,
)
from .validation import Validator

router = APIRouter(prefix="/improvement", tags=["self-development"])


def adapter(request, binding):
    version = request.app.state.candidates.version(binding.version_id)
    manifest = version["manifest"] or {}
    if manifest.get("demo_contract"):
        check_manifest(manifest)
        return DemoSubprocess(request.app.state.settings)
    if manifest.get("test_provenance") == "model_generated_independent":
        return DemoSubprocess(request.app.state.settings)
    return request.app.state.sandbox


@router.post("/review-session")
def session(request: Request) -> dict[str, str]:
    review_origin(request)
    return {"token": request.app.state.review_token}


@router.get("", response_model=Snapshot)
def snapshot(request: Request):
    database = request.app.state.database
    with database.connect() as db:
        evaluations = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM evaluations ORDER BY updated_at DESC LIMIT 200"
            )
        ]
        gaps = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM gaps ORDER BY created_at DESC LIMIT 200"
            )
        ]
        candidates = [
            dict(r)
            for r in db.execute(
                "SELECT c.*,v.id AS version_id,v.version,v.source_hash,v.content_hash,json_extract(v.manifest,'$.name') AS name,s.status,s.error FROM candidates c JOIN candidate_versions v ON v.candidate_id=c.id JOIN candidate_states s ON s.version_id=v.id WHERE v.version=(SELECT MAX(version) FROM candidate_versions WHERE candidate_id=c.id) ORDER BY c.created_at DESC LIMIT 200"
            )
        ]
        active = [
            dict(r)
            for r in db.execute(
                "SELECT a.*,v.source_hash,v.version FROM active_tools a JOIN candidate_versions v ON v.id=a.version_id"
            )
        ]
        audit = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM activation_audit ORDER BY created_at DESC LIMIT 200"
            )
        ]
    return Snapshot(
        evaluations=evaluations,
        gaps=gaps,
        candidates=candidates,
        active=active,
        audit=audit,
        sandbox=request.app.state.sandbox.status(),
        demo=DemoSubprocess(request.app.state.settings).status(),
        limitations=[
            "Completion is not proof of correctness. Grounded business/median contract outputs and exact numeric calculator answers have independent checks; arbitrary prose remains unverified.",
            "Legacy tasks have unknown requested capability/retry counts. Evaluation rules are deterministic-v1; user helpfulness feedback is not correctness verification.",
            "No sensitive prompts, answers, file contents, arguments or preference text are included in this dashboard telemetry.",
            "Lists are bounded to 200; source/task records remain in local SQLite. Explicit demo tasks can automatically approve tested median or eligible business candidates. No model-weight learning or deployment.",
        ],
    )


@router.post("/refresh", response_model=Snapshot)
def refresh(request: Request):
    authorize(request)
    idle(request)
    request.app.state.evaluator.refresh()
    return snapshot(request)


@router.post("/feedback/{task_id}")
def feedback(task_id: UUID, body: FeedbackInput, request: Request) -> dict[str, bool]:
    authorize(request)
    idle(request)
    database = request.app.state.database
    database.task(task_id)
    with database.connect() as db:
        db.execute(
            "INSERT INTO feedback VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET rating=excluded.rating,created_at=excluded.created_at",
            (str(task_id), body.rating, now()),
        )
    request.app.state.evaluator.refresh()
    return {"saved": True}


@router.post("/candidates", response_model=CandidateDetail, status_code=201)
async def candidate(body: GenerateInput, request: Request):
    authorize(request)
    idle(request)
    async with request.app.state.chat_lock:
        return await generate(
            request.app.state.candidates, request.app.state.llm, body.gap_id
        )


@router.post("/draft-tool", status_code=201)
async def draft_tool(body: DraftToolInput, request: Request):
    """Human-initiated: draft a new model-tested tool for review. Never activates it."""
    authorize(request)
    idle(request)
    settings = request.app.state.settings
    if not settings.demo_mode:
        raise LLMError(
            403,
            "demo_disabled",
            "Tool drafting runs model-written code in the DEMO subprocess. Set DEMO_MODE=true in .env and restart the backend.",
        )
    events: list[ActivityEvent] = []

    def record(kind, message, **extra):
        events.append(
            ActivityEvent(sequence=len(events) + 1, kind=kind, message=message, **extra)
        )

    async with request.app.state.chat_lock:
        status = await request.app.state.llm.model_status()
        if status.status != "ready":
            raise LLMError(503, status.status, status.message)
        try:
            outcome = await build_general(
                settings,
                request.app.state.llm,
                request.app.state.database,
                record,
                body.description,
                "requested_tool",
                {},
                str(uuid4()),
            )
        except (Conflict, LLMError) as problem:
            error = (
                problem
                if isinstance(problem, LLMError)
                else LLMError(422, "candidate_build_failed", str(problem))
            )
            error.activity = events
            raise error from problem
    return {"outcome": outcome, "events": [e.model_dump(mode="json") for e in events]}


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def detail(candidate_id: UUID, request: Request):
    return request.app.state.candidates.detail(candidate_id)


@router.post(
    "/candidates/{candidate_id}/versions",
    response_model=CandidateDetail,
    status_code=201,
)
def revision(candidate_id: UUID, body: RevisionInput, request: Request):
    authorize(request)
    idle(request)
    repository = request.app.state.candidates
    repository.append(candidate_id, body, body.base_version_id)
    return repository.detail(candidate_id)


@router.post("/contract", response_model=CandidateDetail)
def contract(body: ContractInput, request: Request):
    actor = authorize(request)
    idle(request)
    return Validator(request.app.state.candidates, request.app.state.sandbox).contract(
        body, actor
    )


@router.post("/validate", response_model=RunResponse)
async def validate(body: Binding, request: Request):
    authorize(request)
    idle(request)
    async with request.app.state.chat_lock:
        return await run_in_threadpool(
            Validator(request.app.state.candidates, adapter(request, body)).validate,
            body,
        )


@router.post("/decision", response_model=CandidateDetail)
def decision(body: DecisionInput, request: Request):
    actor = authorize(request)
    idle(request)
    return Activation(request.app.state.candidates, adapter(request, body)).decide(
        body, actor
    )
