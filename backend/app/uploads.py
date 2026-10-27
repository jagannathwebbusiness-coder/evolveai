import re
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .config import Settings
from .schemas import SheetInfo, UploadResponse
from .storage import Database, MissingRecord, encode, now
from .tools.csv_analysis import parse_csv
from .tools.errors import ToolError
from .tools.excel import parse_xlsx

UPLOAD_SUFFIXES = {".csv", ".xlsx"}


@dataclass(frozen=True)
class UploadedCSV:
    task_id: UUID
    upload_id: UUID
    filename: str
    path: Path
    size_bytes: int
    conversation_id: UUID | None = None


class UploadStore:
    """Persistent handles with conversation scope, or legacy single-task scope."""

    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.root = settings.evolve_workspace_dir.resolve()
        self.records: dict[UUID, UploadedCSV] = {}
        self.lock = threading.Lock()
        self.database = database or Database(settings)
        if database is None:
            self.database.initialize()

    def directory(self) -> Path:
        directory = self.root / "uploads"
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root):
            raise ToolError(
                "unsafe_upload_path",
                "The workspace uploads directory must not point outside the workspace.",
            )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save(
        self,
        data: bytes,
        filename: str,
        task_id: UUID,
        conversation_id: UUID | None = None,
    ) -> UploadResponse:
        if conversation_id:
            self.database.conversation(conversation_id)
        # The supplied filename is a display label only, never part of the storage path.
        if (
            not filename
            or len(filename) > 150
            or Path(filename.lower()).suffix not in UPLOAD_SUFFIXES
            or any(char in filename for char in "/\\:\x00")
            or filename in {".", ".."}
            or any(ord(char) < 32 for char in filename)
            or re.match(
                r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\.", filename, re.IGNORECASE
            )
        ):
            raise ToolError(
                "invalid_filename",
                "Choose a .csv or .xlsx filename without directory paths or reserved Windows names.",
            )
        suffix = Path(filename.lower()).suffix
        if suffix == ".xlsx":
            frames = parse_xlsx(data, self.settings)
            sheets = [
                SheetInfo(name=name, row_count=len(item), columns=list(item.columns))
                for name, item in frames.items()
            ]
            frame = next(iter(frames.values()))
        else:
            sheets = []
            frame = parse_csv(data, self.settings)
        with self.lock:
            directory = self.directory()
            stored = sum(len(list(directory.glob("*" + s))) for s in UPLOAD_SUFFIXES)
            if stored >= self.settings.upload_max_files:
                raise ToolError(
                    "upload_storage_limit",
                    "Workspace upload limit reached. Remove unneeded CSV files manually before uploading more.",
                )
            upload_id = uuid4()
            path = directory / f"{upload_id.hex}{suffix}"
            with path.open("xb") as handle:
                handle.write(data)
            self.records[upload_id] = UploadedCSV(
                task_id, upload_id, filename, path, len(data), conversation_id
            )
            try:
                self.database.add_file(
                    {
                        "id": str(upload_id),
                        "kind": "upload",
                        "conversation_id": (
                            str(conversation_id) if conversation_id else None
                        ),
                        "task_id": str(task_id),
                        "filename": filename,
                        "relative_path": path.relative_to(self.root).as_posix(),
                        "size_bytes": len(data),
                        "metadata": encode(
                            {
                                "row_count": len(frame),
                                "columns": list(frame.columns),
                                "sheets": [item.model_dump() for item in sheets],
                            }
                        ),
                        "created_at": now(),
                    }
                )
            except Exception:
                path.unlink(missing_ok=True)
                self.records.pop(upload_id, None)
                raise
        return UploadResponse(
            task_id=task_id,
            upload_id=upload_id,
            filename=filename,
            size_bytes=len(data),
            row_count=len(frame),
            columns=list(frame.columns),
            conversation_id=conversation_id,
            kind="xlsx" if suffix == ".xlsx" else "csv",
            sheets=sheets,
        )

    def get(
        self, upload_id: UUID, task_id: UUID, conversation_id: UUID | None = None
    ) -> UploadedCSV:
        try:
            saved = self.database.file(upload_id)
        except MissingRecord as exc:
            self.records.pop(upload_id, None)
            raise ToolError("upload_not_found", str(exc)) from exc
        record = self.records.get(upload_id)
        if record is None and saved["kind"] == "upload":
            record = UploadedCSV(
                UUID(saved["task_id"]),
                upload_id,
                saved["filename"],
                self.root / saved["relative_path"],
                saved["size_bytes"],
                UUID(saved["conversation_id"]) if saved["conversation_id"] else None,
            )
        authorized = record is not None and (
            (conversation_id is not None and record.conversation_id == conversation_id)
            or (
                conversation_id is None
                and record.conversation_id is None
                and record.task_id == task_id
            )
        )
        if not authorized:
            raise ToolError(
                "upload_not_found",
                "This CSV is not associated with the current conversation/task. Select a file belonging to this conversation or upload it here.",
            )
        expected = self.root / "uploads" / f"{upload_id.hex}{record.path.suffix}"
        if (
            record.path.suffix not in UPLOAD_SUFFIXES
            or record.path != expected
            or record.path.is_symlink()
            or not record.path.resolve().is_relative_to(self.root)
            or not record.path.is_file()
        ):
            raise ToolError(
                "unsafe_upload_path",
                "The uploaded CSV is missing or no longer safely inside the workspace. Upload it again.",
            )
        return record

    def read(
        self, upload_id: UUID, task_id: UUID, conversation_id: UUID | None = None
    ) -> bytes:
        record = self.get(upload_id, task_id, conversation_id)
        with record.path.open("rb") as handle:
            data = handle.read(self.settings.upload_max_bytes + 1)
        return data
