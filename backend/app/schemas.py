from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_CHARS = 6000
MAX_MESSAGES = 20
MAX_REQUEST_BYTES = 64 * 1024


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    # Kept so older clients still validate; EVOLVE always runs the agentic loop.
    use_tools: bool = True
    agentic: bool = True
    demo_tools: bool = True
    tool_choice: Literal[
        "auto",
        "calculate",
        "analyze_csv",
        "list_workspace_files",
        "read_workspace_file",
        "create_report",
    ] = "auto"
    task_id: UUID | None = None
    conversation_id: UUID | None = None
    retry_of: UUID | None = None
    upload_ids: list[UUID] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def validate_history(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user.")
        if sum(len(message.content) for message in self.messages) > MAX_HISTORY_CHARS:
            raise ValueError(f"Chat context exceeds {MAX_HISTORY_CHARS} characters.")
        if (self.upload_ids or self.tool_choice != "auto") and not self.use_tools:
            raise ValueError("Enable use_tools for attachments or a selected tool.")
        if self.upload_ids and self.task_id is None:
            raise ValueError("Uploads require their associated task_id.")
        if self.tool_choice == "analyze_csv" and not self.upload_ids:
            raise ValueError("Upload a CSV before selecting analyze_csv.")
        if (
            self.tool_choice
            in {"list_workspace_files", "read_workspace_file", "create_report"}
            and not self.conversation_id
        ):
            raise ValueError("Workspace tools require a saved conversation.")
        return self


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "evolve-backend"
    phase: int = 4


class ModelStatus(BaseModel):
    status: Literal[
        "ready",
        "not_configured",
        "invalid_key",
        "model_missing",
        "unreachable",
        "error",
    ]
    provider: str
    api_key_configured: bool
    provider_reachable: bool
    model_available: bool | None
    model: str
    base_url: str
    message: str


class ActivityEvent(BaseModel):
    sequence: int
    kind: str
    message: str
    iteration: int | None = None
    tool: str | None = None
    call_id: str | None = None
    arguments: dict | None = None
    result: dict | None = None


class ChatResponse(BaseModel):
    model: str
    message: str
    duration_ms: int
    truncated: bool
    task_id: UUID | None = None
    conversation_id: UUID | None = None
    activity: list[ActivityEvent] = Field(default_factory=list)


class SheetInfo(BaseModel):
    name: str
    row_count: int
    columns: list[str]


class UploadResponse(BaseModel):
    task_id: UUID
    upload_id: UUID
    filename: str
    size_bytes: int
    row_count: int
    columns: list[str]
    conversation_id: UUID | None = None
    kind: Literal["csv", "xlsx"] = "csv"
    sheets: list[SheetInfo] = Field(default_factory=list)


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="New conversation", min_length=1, max_length=100)


class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: Literal[
        "report_format",
        "units",
        "output_style",
        "workspace_directory",
        "task_instructions",
    ]
    value: str = Field(min_length=1, max_length=800)
    keywords: str = Field(default="", max_length=200)
    conversation_id: UUID | None = None
    approved: Literal[True]


class LLMMessage(BaseModel):
    role: Literal["assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def require_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The model returned an empty answer.")
        return value


class LLMChat(BaseModel):
    model: str = Field(min_length=1)
    message: LLMMessage
    done: Literal[True]
    done_reason: str | None = None
