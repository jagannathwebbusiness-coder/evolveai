"""Local SQLite repository. Connections are short-lived and writes are transactional."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .config import Settings
from .schemas import ChatMessage


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class MissingRecord(Exception):
    pass


class Conflict(Exception):
    pass


class Database:
    VERSION = 3

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.evolve_data_dir / "evolve.sqlite3"

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, recover=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > self.VERSION:
                raise RuntimeError(
                    "Database is newer than this app. Restore the matching app version."
                )
            # DDL and user_version are committed together; never drop existing tables.
            db.execute("BEGIN IMMEDIATE")
            if version < 1:
                for sql in [
                    "CREATE TABLE conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE tasks(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, duration_ms INTEGER, model TEXT, error TEXT, memory TEXT NOT NULL DEFAULT '[]')",
                    "CREATE TABLE messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}')",
                    "CREATE TABLE events(task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,sequence))",
                    "CREATE TABLE preferences(id TEXT PRIMARY KEY, key TEXT NOT NULL, value TEXT NOT NULL, keywords TEXT NOT NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE INDEX messages_conversation ON messages(conversation_id,seq)",
                    "CREATE INDEX tasks_conversation ON tasks(conversation_id,created_at)",
                    "CREATE INDEX preferences_scope ON preferences(conversation_id,key)",
                ]:
                    db.execute(sql)
                db.execute("PRAGMA user_version=1")
            if version < 2:
                db.execute(
                    "CREATE TABLE files(id TEXT PRIMARY KEY, kind TEXT NOT NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, task_id TEXT, filename TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE task_files(task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, PRIMARY KEY(task_id,file_id))"
                )
                db.execute(
                    "CREATE INDEX files_conversation ON files(conversation_id,created_at)"
                )
                db.execute("PRAGMA user_version=2")
            if version < 3:
                from .improvement.persistence import migrate

                migrate(db)
                db.execute("PRAGMA user_version=3")
            if recover:
                db.execute(
                    "UPDATE tasks SET status='interrupted',error=?,updated_at=? WHERE status='running'",
                    (
                        "Backend stopped before this task finished. Review tool results before retrying.",
                        now(),
                    ),
                )

    def conversation(self, identifier):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?", (str(identifier),)
            ).fetchone()
            if row is None:
                raise MissingRecord(
                    "Conversation not found. Select an existing conversation or create a new one."
                )
            return dict(row)

    def create_conversation(self, title="New conversation"):
        identifier, stamp = str(uuid4()), now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO conversations VALUES(?,?,?,?)",
                (identifier, title, stamp, stamp),
            )
        return self.conversation(identifier)

    def conversations(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM conversations ORDER BY updated_at DESC,id LIMIT 200"
                )
            ]

    def rename(self, identifier, title):
        self.conversation(identifier)
        with self.connect() as db:
            db.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                (title, now(), str(identifier)),
            )
        return self.conversation(identifier)

    def delete_conversation(self, identifier):
        self.conversation(identifier)
        with self.connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (str(identifier),))
        # Files are retained as detached records; explicit file cleanup is separate.

    def detail(self, identifier):
        result = self.conversation(identifier)
        with self.connect() as db:
            result["messages"] = [
                dict(row) | {"metadata": json.loads(row["metadata"])}
                for row in db.execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 200",
                    (str(identifier),),
                )
            ][::-1]
            result["tasks"] = [
                self.task_row(db, row)
                for row in db.execute(
                    "SELECT * FROM tasks WHERE conversation_id=? ORDER BY created_at DESC LIMIT 100",
                    (str(identifier),),
                )
            ]
        return result

    def task_row(self, db, row):
        result = dict(row)
        result["memory"] = json.loads(result["memory"])
        result["activity"] = [
            json.loads(item[0])
            for item in db.execute(
                "SELECT payload FROM events WHERE task_id=? ORDER BY sequence",
                (row["id"],),
            )
        ]
        return result

    def task(self, identifier):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=?", (str(identifier),)
            ).fetchone()
            if row is None:
                raise MissingRecord("Task not found.")
            return self.task_row(db, row)

    def history(self, conversation_id, current):
        with self.connect() as db:
            rows = db.execute(
                "SELECT role,content FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT ?",
                (str(conversation_id), self.settings.history_max_messages - 1),
            ).fetchall()
        remaining = self.settings.history_max_chars - len(current.content)
        messages = []
        for row in rows:
            content = row["content"][:2000]
            if len(content) > remaining:
                break
            messages.insert(0, ChatMessage(role=row["role"], content=content))
            remaining -= len(content)
        while messages and messages[0].role == "assistant":
            messages.pop(0)
        return messages + [current]

    def begin(self, task_id, conversation_id, user, memory, upload_ids):
        stamp = now()
        try:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO tasks(id,conversation_id,status,created_at,updated_at,memory) VALUES(?,?,'running',?,?,?)",
                    (str(task_id), str(conversation_id), stamp, stamp, encode(memory)),
                )
                db.execute(
                    "INSERT INTO messages(id,conversation_id,task_id,role,content,created_at) VALUES(?,?,?,'user',?,?)",
                    (
                        str(uuid4()),
                        str(conversation_id),
                        str(task_id),
                        user.content,
                        stamp,
                    ),
                )
                db.execute(
                    "UPDATE conversations SET updated_at=?,title=CASE WHEN title='New conversation' THEN ? ELSE title END WHERE id=?",
                    (stamp, user.content[:80], str(conversation_id)),
                )
                for identifier in upload_ids:
                    db.execute(
                        "INSERT INTO task_files VALUES(?,?)",
                        (str(task_id), str(identifier)),
                    )
        except sqlite3.IntegrityError as exc:
            raise Conflict(
                "Task ID already exists or its context changed. Start a new request with a new task ID."
            ) from exc

    def event(self, task_id, event):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events VALUES(?,?,?)",
                (str(task_id), event.sequence, encode(event.model_dump(mode="json"))),
            )
            db.execute(
                "UPDATE tasks SET updated_at=? WHERE id=?", (now(), str(task_id))
            )

    def finish(self, task_id, response=None, error=None, duration_ms=0):
        with self.connect() as db:
            task = db.execute(
                "SELECT conversation_id FROM tasks WHERE id=?", (str(task_id),)
            ).fetchone()
            status = (
                "failed"
                if error
                else (
                    "completed_with_errors"
                    if db.execute(
                        "SELECT 1 FROM events WHERE task_id=? AND json_extract(payload,'$.kind')='tool_failed' LIMIT 1",
                        (str(task_id),),
                    ).fetchone()
                    else "completed"
                )
            )
            if (
                status == "completed_with_errors"
                and db.execute(
                    "SELECT 1 FROM events WHERE task_id=? AND (json_extract(payload,'$.kind')='recovery_succeeded' OR (json_extract(payload,'$.kind')='goal_verified' AND json_extract(payload,'$.result.passed')=1))",
                    (str(task_id),),
                ).fetchone()
            ):
                status = "completed_recovered"
            stamp = now()
            db.execute(
                "UPDATE tasks SET status=?,updated_at=?,duration_ms=?,model=?,error=? WHERE id=?",
                (
                    status,
                    stamp,
                    response.duration_ms if response else duration_ms,
                    response.model if response else self.settings.llm_model,
                    error,
                    str(task_id),
                ),
            )
            if response:
                metadata = {
                    "model": response.model,
                    "duration_ms": response.duration_ms,
                    "truncated": response.truncated,
                }
                db.execute(
                    "INSERT INTO messages(id,conversation_id,task_id,role,content,created_at,metadata) VALUES(?,?,?,'assistant',?,?,?)",
                    (
                        str(uuid4()),
                        task[0],
                        str(task_id),
                        response.message,
                        stamp,
                        encode(metadata),
                    ),
                )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (stamp, task[0])
            )

    def preferences(self, conversation_id=None, all_scopes=False):
        with self.connect() as db:
            rows = (
                db.execute("SELECT * FROM preferences ORDER BY created_at,id")
                if all_scopes
                else db.execute(
                    "SELECT * FROM preferences WHERE conversation_id IS NULL OR conversation_id=? ORDER BY conversation_id IS NOT NULL DESC,updated_at DESC",
                    (str(conversation_id),),
                )
            )
            return [dict(row) for row in rows]

    def save_preference(self, body, identifier=None):
        if body.conversation_id:
            self.conversation(body.conversation_id)
        stamp = now()
        with self.connect() as db:
            if identifier:
                if not db.execute(
                    "SELECT 1 FROM preferences WHERE id=?", (str(identifier),)
                ).fetchone():
                    raise MissingRecord("Preference not found.")
                db.execute(
                    "UPDATE preferences SET key=?,value=?,keywords=?,conversation_id=?,updated_at=? WHERE id=?",
                    (
                        body.key,
                        body.value,
                        body.keywords,
                        str(body.conversation_id) if body.conversation_id else None,
                        stamp,
                        str(identifier),
                    ),
                )
            else:
                identifier = str(uuid4())
                db.execute(
                    "INSERT INTO preferences VALUES(?,?,?,?,?,?,?)",
                    (
                        identifier,
                        body.key,
                        body.value,
                        body.keywords,
                        str(body.conversation_id) if body.conversation_id else None,
                        stamp,
                        stamp,
                    ),
                )
            return dict(
                db.execute(
                    "SELECT * FROM preferences WHERE id=?", (str(identifier),)
                ).fetchone()
            )

    def delete_preference(self, identifier):
        with self.connect() as db:
            if not db.execute(
                "DELETE FROM preferences WHERE id=?", (str(identifier),)
            ).rowcount:
                raise MissingRecord("Preference not found.")

    def memory(self, conversation_id, prompt):
        chosen, seen, budget = [], set(), self.settings.memory_max_chars
        for pref in self.preferences(conversation_id):
            terms = pref["keywords"].lower().split()
            if pref["key"] in seen or (
                terms and not any(term in prompt.lower() for term in terms)
            ):
                continue
            size = len(pref["key"]) + len(pref["value"]) + 30
            if size > budget:
                continue
            chosen.append(
                {"id": pref["id"], "key": pref["key"], "value": pref["value"]}
            )
            seen.add(pref["key"])
            budget -= size
        with self.connect() as db:
            previous = db.execute(
                "SELECT id,status FROM tasks WHERE conversation_id=? ORDER BY created_at DESC LIMIT 2",
                (str(conversation_id),),
            ).fetchall()
        return chosen, [dict(row) for row in previous]

    def add_file(self, record):
        with self.connect() as db:
            db.execute(
                "INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                tuple(
                    record[key]
                    for key in (
                        "id",
                        "kind",
                        "conversation_id",
                        "task_id",
                        "filename",
                        "relative_path",
                        "size_bytes",
                        "metadata",
                        "created_at",
                    )
                ),
            )
            if (
                record["task_id"]
                and db.execute(
                    "SELECT 1 FROM tasks WHERE id=?", (record["task_id"],)
                ).fetchone()
            ):
                db.execute(
                    "INSERT OR IGNORE INTO task_files VALUES(?,?)",
                    (record["task_id"], record["id"]),
                )

    def file(self, identifier):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM files WHERE id=?", (str(identifier),)
            ).fetchone()
            if row is None:
                raise MissingRecord(
                    "File handle not found. It may have been explicitly removed or predates Phase 3."
                )
            return dict(row) | {"metadata": json.loads(row["metadata"])}

    def files(self, conversation_id=None):
        with self.connect() as db:
            rows = (
                db.execute(
                    "SELECT id FROM files WHERE conversation_id=? ORDER BY created_at DESC LIMIT 200",
                    (str(conversation_id),),
                )
                if conversation_id
                else db.execute(
                    "SELECT id FROM files ORDER BY created_at DESC LIMIT 200"
                )
            )
            identifiers = [row[0] for row in rows]
        return [self.file(identifier) for identifier in identifiers]

    def remove_file(self, identifier):
        with self.connect() as db:
            db.execute("DELETE FROM files WHERE id=?", (str(identifier),))
