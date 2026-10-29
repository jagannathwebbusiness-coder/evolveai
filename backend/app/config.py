from pathlib import Path
from urllib.parse import urlsplit

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

# Provider presets: (OpenAI-compatible base URL, default model). Override with LLM_BASE_URL/LLM_MODEL.
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "qwen/qwen3.8-27b"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.5-flash",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
    ),
    "custom": ("", ""),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Hosted OpenAI-compatible chat API. Free tiers: groq, gemini, openrouter.
    llm_provider: Literal["groq", "gemini", "openrouter", "custom"] = "groq"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60, ge=1, le=300)
    llm_status_timeout_seconds: float = Field(default=10, ge=1, le=60)
    llm_max_output_tokens: int = Field(default=1024, ge=64, le=8192)
    llm_max_retries: int = Field(default=3, ge=0, le=6)
    agent_max_iterations: int = Field(default=6, ge=2, le=12)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=24)
    agent_timeout_seconds: float = Field(default=180, ge=1, le=600)
    evolve_workspace_dir: Path = ROOT / "workspace"
    evolve_data_dir: Path = ROOT / "data"
    history_max_messages: int = Field(default=12, ge=2, le=20)
    history_max_chars: int = Field(default=5000, ge=2000, le=6000)
    memory_max_chars: int = Field(default=1200, ge=200, le=2400)
    workspace_max_bytes: int = Field(default=32768, ge=128, le=49152)
    workspace_max_files: int = Field(default=100, ge=1, le=500)
    upload_max_bytes: int = Field(default=1024 * 1024, ge=128, le=10 * 1024 * 1024)
    csv_max_rows: int = Field(default=10000, ge=1, le=100000)
    csv_max_columns: int = Field(default=50, ge=1, le=100)
    upload_max_files: int = Field(default=50, ge=1, le=500)
    xlsx_max_uncompressed_bytes: int = Field(
        default=30 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024
    )
    xlsx_max_sheets: int = Field(default=20, ge=1, le=100)
    improvement_min_evidence: int = Field(default=3, ge=1, le=100)
    candidate_max_tokens: int = Field(default=2048, ge=256, le=8192)
    sandbox_enabled: bool = False
    # Explicitly opted-in local Docker Desktop Linux context; never WSL subprocess fallback.
    sandbox_image: str = Field(default="", max_length=180)
    sandbox_timeout_seconds: float = Field(default=5, ge=1, le=20)
    # Explicit educational opt-in. A host subprocess IS NOT a secure sandbox.
    demo_mode: bool = False
    demo_timeout_seconds: float = Field(default=5, ge=1, le=15)
    # Windows job-object cap for each demo child process (no effect elsewhere).
    demo_memory_mb: int = Field(default=256, ge=64, le=2048)
    # A second model call scores each tool-backed answer (Reviewer agent).
    reviewer_enabled: bool = False
    general_tools_max_per_task: int = Field(default=1, ge=0, le=3)
    agentic_max_recoveries: int = Field(default=3, ge=1, le=5)
    agentic_build_attempts: int = Field(default=2, ge=1, le=3)

    @field_validator("evolve_workspace_dir", "evolve_data_dir")
    @classmethod
    def workspace_path(cls, value: Path) -> Path:
        # Relative configuration is anchored to the project, never the launch directory.
        return (value if value.is_absolute() else ROOT / value).resolve()

    @model_validator(mode="after")
    def provider_defaults(self):
        base, model = PROVIDERS[self.llm_provider]
        self.llm_base_url = (self.llm_base_url.strip() or base).rstrip("/")
        self.llm_model = self.llm_model.strip() or model
        url = urlsplit(self.llm_base_url)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or (url.scheme == "http" and not local)
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "LLM_BASE_URL must be an https URL (http only for localhost) with no credentials."
            )
        _ = url.port  # Validate malformed port numbers at startup.
        if not self.llm_model:
            raise ValueError("Set LLM_MODEL when LLM_PROVIDER=custom.")
        return self
