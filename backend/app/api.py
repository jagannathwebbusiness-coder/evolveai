"""Persistence and workspace endpoints for this single-user, loopback application."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .llm_client import LLMError
from .schemas import ConversationInput, PreferenceInput
from .tools import knowledge
from .tools.errors import ToolError
from .tools.workspace_files import (
    ListFilesArgs,
    ReadFileArgs,
    ReportArgs,
    WorkspaceFiles,
)

router = APIRouter()


def idle(request):
    if request.app.state.chat_lock.locked():
        raise LLMError(
            409,
            "task_running",
            "Wait for the current task to finish before changing conversations or files.",
        )


def confirmed(confirm):
    if not confirm:
        raise LLMError(
            400,
            "confirmation_required",
            "Confirm this deletion explicitly with confirm=true.",
        )


def workspace(request, conversation_id=None, task_id=None):
    database = request.app.state.database
    if conversation_id:
        database.conversation(conversation_id)
    preferences, _ = database.memory(conversation_id, "")
    return WorkspaceFiles(
        request.app.state.settings, database, conversation_id, task_id, preferences
    )


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationInput, request: Request):
    return request.app.state.database.create_conversation(body.title)


@router.get("/conversations")
def conversations(request: Request):
    return request.app.state.database.conversations()


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, request: Request):
    return request.app.state.database.detail(conversation_id)


@router.patch("/conversations/{conversation_id}")
def rename(conversation_id: UUID, body: ConversationInput, request: Request):
    idle(request)
    return request.app.state.database.rename(conversation_id, body.title)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: UUID, request: Request, confirm: bool = False):
    confirmed(confirm)
    idle(request)
    request.app.state.database.delete_conversation(conversation_id)
    return {"deleted": True, "files_retained": True}


@router.get("/tasks/{task_id}")
def task(task_id: UUID, request: Request):
    return request.app.state.database.task(task_id)


@router.get("/preferences")
def preferences(request: Request):
    return request.app.state.database.preferences(all_scopes=True)


def validate_preference(body, request):
    if body.key == "report_format" and body.value not in {"md", "txt", "json", "csv"}:
        raise ToolError(
            "invalid_preference", "Report format must be md, txt, json or csv."
        )
    if body.key == "workspace_directory":
        path = workspace(request).resolve(body.value)
        if any(
            part in {"uploads", "reports"}
            for part in path.relative_to(
                request.app.state.settings.evolve_workspace_dir
            ).parts
        ):
            raise ToolError(
                "invalid_preference",
                "Choose a subdirectory outside managed uploads/reports.",
            )
        if path.exists() and not path.is_dir():
            raise ToolError(
                "invalid_preference",
                "Default workspace must be a directory inside EVOLVE_WORKSPACE_DIR.",
            )


@router.post("/preferences", status_code=201)
def save_preference(body: PreferenceInput, request: Request):
    validate_preference(body, request)
    if len(request.app.state.database.preferences(all_scopes=True)) >= 100:
        raise LLMError(
            409,
            "preference_limit",
            "Preference limit reached. Remove obsolete preferences first.",
        )
    return request.app.state.database.save_preference(body)


@router.put("/preferences/{preference_id}")
def update_preference(preference_id: UUID, body: PreferenceInput, request: Request):
    validate_preference(body, request)
    return request.app.state.database.save_preference(body, preference_id)


@router.delete("/preferences/{preference_id}")
def delete_preference(preference_id: UUID, request: Request, confirm: bool = False):
    confirmed(confirm)
    request.app.state.database.delete_preference(preference_id)
    return {"deleted": True}


@router.get("/files")
def files(request: Request, conversation_id: UUID | None = None):
    service = workspace(request, conversation_id)
    records = request.app.state.database.files(conversation_id)
    for record in records:
        try:
            record["available"] = service.managed_path(
                record, check_owner=False
            ).is_file()
        except ToolError:
            record["available"] = False
    return records


@router.get("/workspace/files")
def workspace_files(
    request: Request,
    directory: str = Query(default=".", min_length=1, max_length=240),
    conversation_id: UUID | None = None,
):
    return workspace(request, conversation_id).list_files(
        ListFilesArgs(directory=directory)
    )


@router.get("/workspace/read")
def read_file(
    request: Request,
    path: str = Query(min_length=1, max_length=240),
    conversation_id: UUID | None = None,
):
    return workspace(request, conversation_id).read(ReadFileArgs(path=path))


@router.post("/reports", status_code=201)
def report(body: ReportArgs, request: Request, conversation_id: UUID):
    idle(request)
    return workspace(request, conversation_id).create_report(body)


@router.post("/tasks/{task_id}/export", status_code=201)
def export_task(
    task_id: UUID,
    request: Request,
    conversation_id: UUID,
    format: Literal["json", "csv"] = "json",
):
    idle(request)
    return workspace(request, conversation_id, task_id).export_task(task_id, format)


@router.get("/reports/{file_id}/download")
def download(file_id: UUID, request: Request, conversation_id: UUID):
    service = workspace(request, conversation_id)
    record = request.app.state.database.file(file_id)
    if record["kind"] != "report":
        raise ToolError(
            "not_a_report", "Only generated reports can be downloaded here."
        )
    path = service.managed_path(record)
    if not path.is_file():
        raise ToolError(
            "workspace_file_missing",
            "Report is missing from the workspace. Its metadata is retained for inspection or explicit cleanup.",
        )
    if path.stat().st_size > request.app.state.settings.workspace_max_bytes:
        raise ToolError(
            "report_too_large", "Report file exceeds the configured size limit."
        )
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=record["filename"],
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/files/{file_id}")
def delete_file(file_id: UUID, request: Request, confirm: bool = False):
    confirmed(confirm)
    idle(request)
    workspace(request).cleanup(file_id)
    request.app.state.uploads.records.pop(file_id, None)
    return {"deleted": True}


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=knowledge.MAX_NOTE_CHARS)


@router.get("/knowledge")
def knowledge_notes(request: Request):
    return knowledge.list_notes(request.app.state.settings)


@router.post("/knowledge", status_code=201)
def add_knowledge(body: NoteInput, request: Request):
    return knowledge.add_note(request.app.state.settings, body.title, body.content)


@router.delete("/knowledge/{note_id}")
def delete_knowledge(note_id: str, request: Request):
    knowledge.delete_note(request.app.state.settings, note_id)
    return {"deleted": note_id}
