"""Bounded UTF-8 workspace operations. Caller labels never become storage paths."""

import csv
import io
import json
from pathlib import Path, PureWindowsPath
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..storage import Database, MissingRecord, encode, now
from .csv_analysis import parse_csv
from .errors import ToolError

SUPPORTED = {".txt", ".md", ".csv", ".json"}


class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory: str = Field(default=".", min_length=1, max_length=240)


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)


class ReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Report", min_length=1, max_length=100)
    format: Literal["md", "txt", "json", "csv"] | None = None
    content: str = Field(min_length=1, max_length=24000)


class WorkspaceFiles:
    def __init__(
        self,
        settings,
        database: Database,
        conversation_id=None,
        task_id=None,
        preferences=None,
    ):
        self.settings, self.database = settings, database
        self.root = settings.evolve_workspace_dir.resolve()
        self.conversation_id = str(conversation_id) if conversation_id else None
        self.task_id = str(task_id) if task_id else None
        self.preferences = {item["key"]: item["value"] for item in preferences or []}

    def resolve(self, relative: str, must_exist=False):
        # Windows drive/UNC/ADS and both traversal separators are rejected on all OSes.
        windows = PureWindowsPath(relative)
        parts = relative.replace("\\", "/").split("/")
        if (
            windows.drive
            or windows.root
            or Path(relative).is_absolute()
            or ":" in relative
            or ".." in parts
            or any(ord(char) < 32 for char in relative)
        ):
            raise ToolError(
                "unsafe_workspace_path",
                "Use a relative path inside EVOLVE_WORKSPACE_DIR without traversal or drive names.",
            )
        candidate = self.root.joinpath(*parts)
        current = self.root
        for part in parts:
            current /= part
            if current.is_symlink() or current.is_junction():
                raise ToolError(
                    "unsafe_workspace_path",
                    "Workspace links and redirected directories are not allowed.",
                )
        if not candidate.resolve().is_relative_to(self.root):
            raise ToolError(
                "unsafe_workspace_path", "The resolved file is outside the workspace."
            )
        if must_exist and not candidate.exists():
            raise ToolError(
                "workspace_file_missing",
                "Workspace file is missing. Refresh files or select another file.",
            )
        return candidate

    def authorized(self, path):
        relative = path.relative_to(self.root).as_posix()
        with self.database.connect() as db:
            row = db.execute(
                "SELECT conversation_id FROM files WHERE relative_path=?", (relative,)
            ).fetchone()
        if row is not None:
            if self.conversation_id is None or row[0] != self.conversation_id:
                raise ToolError(
                    "file_wrong_context",
                    "This file belongs to another conversation or is detached. Attach it in the correct conversation.",
                )
        elif any(
            part in {"uploads", "reports"} for part in path.relative_to(self.root).parts
        ):
            raise ToolError(
                "file_not_registered",
                "This managed file has no metadata. Older uploads are retained but must be uploaded again to authorize them.",
            )

    def list_files(self, args: ListFilesArgs):
        relative = (
            self.preferences.get("workspace_directory", ".")
            if args.directory == "."
            else args.directory
        )
        directory = self.resolve(relative)
        if not directory.exists():
            return {"files": [], "directory": relative, "truncated": False}
        if not directory.is_dir():
            raise ToolError("invalid_directory", "Select a workspace directory.")
        results, inspected = [], 0
        # Bounded shallow listing; avoids recursively scanning an arbitrary workspace.
        for path in directory.iterdir():
            inspected += 1
            if inspected > self.settings.workspace_max_files:
                break
            try:
                safe = self.resolve(path.relative_to(self.root).as_posix(), True)
                if safe.is_dir():
                    if safe.name not in {"uploads", "reports"}:
                        results.append(
                            {
                                "path": safe.relative_to(self.root).as_posix(),
                                "kind": "directory",
                            }
                        )
                elif safe.suffix.lower() in SUPPORTED:
                    self.authorized(safe)
                    results.append(
                        {
                            "path": safe.relative_to(self.root).as_posix(),
                            "kind": "file",
                            "size_bytes": safe.stat().st_size,
                        }
                    )
            except ToolError:
                continue
        return {
            "files": sorted(results, key=lambda item: item["path"]),
            "directory": relative,
            "truncated": inspected > self.settings.workspace_max_files,
        }

    def read(self, args: ReadFileArgs):
        path = self.resolve(args.path, True)
        self.authorized(path)
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            raise ToolError(
                "unsupported_file_type",
                "Read supports UTF-8 .txt, .md, .json and .csv files only.",
            )
        with path.open("rb") as handle:
            data = handle.read(self.settings.workspace_max_bytes + 1)
        if len(data) > self.settings.workspace_max_bytes:
            raise ToolError(
                "workspace_file_too_large",
                "File exceeds WORKSPACE_MAX_BYTES. Use CSV analysis for larger CSV files.",
            )
        try:
            content = data.decode("utf-8-sig")
            if any(ord(char) < 32 and char not in "\n\r\t" for char in content):
                raise ValueError("binary")
        except (UnicodeError, ValueError) as exc:
            raise ToolError(
                "invalid_text_file",
                "File must contain UTF-8 text without binary controls.",
            ) from exc
        return {
            "path": args.path,
            "content": content,
            "size_bytes": len(data),
            "untrusted_data": True,
        }

    def create_report(self, args: ReportArgs):
        if not self.conversation_id:
            raise ToolError(
                "conversation_required",
                "Create or open a conversation before generating reports.",
            )
        self.database.conversation(self.conversation_id)
        format_name = args.format or self.preferences.get("report_format", "md")
        if format_name not in {"md", "txt", "csv", "json"}:
            format_name = "md"
        data = args.content.encode("utf-8")
        if len(data) > self.settings.workspace_max_bytes:
            raise ToolError("report_too_large", "Report exceeds WORKSPACE_MAX_BYTES.")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in args.content):
            raise ToolError(
                "invalid_report", "Reports must be text without binary controls."
            )
        if format_name == "json":
            try:
                json.loads(
                    args.content,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        ValueError("nonfinite")
                    ),
                )
            except ValueError as exc:
                raise ToolError(
                    "invalid_report_json",
                    "JSON reports must contain valid finite JSON.",
                ) from exc
        if format_name == "csv":
            parse_csv(data, self.settings)
        identifier = uuid4()
        base = self.preferences.get("workspace_directory", ".")
        directory = self.resolve(f"{base}/reports")
        directory.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in directory.iterdir()) >= self.settings.workspace_max_files:
            raise ToolError(
                "report_storage_limit",
                "Report directory limit reached. Explicitly clean up obsolete reports first.",
            )
        path = self.resolve(f"{base}/reports/{identifier.hex}.{format_name}")
        with path.open("xb") as handle:
            handle.write(data)
        record = {
            "id": str(identifier),
            "kind": "report",
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "filename": f"Report-{identifier.hex[:8]}.{format_name}",
            "relative_path": path.relative_to(self.root).as_posix(),
            "size_bytes": len(data),
            "metadata": encode({"title": args.title, "format": format_name}),
            "created_at": now(),
        }
        try:
            self.database.add_file(record)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {
            "file_id": str(identifier),
            "filename": record["filename"],
            "size_bytes": len(data),
            "download_url": f"/api/reports/{identifier}/download?conversation_id={self.conversation_id}",
        }

    def managed_path(self, record, check_owner=True):
        if check_owner and (
            not self.conversation_id
            or record["conversation_id"] != self.conversation_id
        ):
            raise ToolError(
                "file_wrong_context", "File does not belong to this conversation."
            )
        path = self.resolve(record["relative_path"])
        identifier = UUID(record["id"])
        stored_suffix = Path(record["relative_path"]).suffix
        expected_suffix = (
            stored_suffix
            if record["kind"] == "upload" and stored_suffix in {".csv", ".xlsx"}
            else (
                ".csv"
                if record["kind"] == "upload"
                else "." + record["metadata"]["format"]
            )
        )
        if path.name != identifier.hex + expected_suffix or path.parent.name != (
            "uploads" if record["kind"] == "upload" else "reports"
        ):
            raise ToolError(
                "unsafe_workspace_path",
                "Managed file metadata contains an invalid storage path.",
            )
        return path

    def cleanup(self, file_id):
        record = self.database.file(file_id)
        path = self.managed_path(record, check_owner=False)
        # Only an explicitly confirmed API call may delete this generated, registered file.
        if path.exists() and not path.is_file():
            raise ToolError(
                "unsafe_workspace_path",
                "Cleanup only supports individual managed files.",
            )
        path.unlink(missing_ok=True)
        self.database.remove_file(file_id)

    def export_task(self, task_id, format_name):
        task = self.database.task(task_id)
        if task["conversation_id"] != self.conversation_id:
            raise MissingRecord("Task does not belong to this conversation.")
        results = [
            event
            for event in task["activity"]
            if event["kind"] in {"tool_executed", "tool_failed"}
        ]
        if not results:
            raise ToolError(
                "no_tool_results", "This task has no tool results to export."
            )
        if format_name == "json":
            content = encode({"task_id": str(task_id), "results": results})
        else:
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(["tool", "call_id", "result_json"])
            for event in results:
                writer.writerow(
                    [event["tool"], event["call_id"], encode(event["result"])]
                )
            content = buffer.getvalue()
        return self.create_report(
            ReportArgs(
                title="Verified tool results", format=format_name, content=content
            )
        )
