"""Verify a populated schema-v2 backup on a disposable copy, never the source DB."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.storage import Database  # noqa: E402

TABLES = (
    "conversations",
    "tasks",
    "messages",
    "events",
    "preferences",
    "files",
    "task_files",
)


def snapshot(connection):
    result = {}
    for table in TABLES:
        rows = [
            tuple(r)
            for r in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]
        result[table] = {
            "rows": len(rows),
            "sha256": hashlib.sha256(
                json.dumps(rows, ensure_ascii=False).encode()
            ).hexdigest(),
        }
    return result


def main():
    source = Path(sys.argv[1]).resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        assert (
            old.execute("PRAGMA user_version").fetchone()[0] == 2
        ), "Source must be an existing schema-v2 backup."
        before = snapshot(old)
        with tempfile.TemporaryDirectory(prefix="evolve-migration-") as temporary:
            settings = Settings(_env_file=None, evolve_data_dir=Path(temporary))
            database = Database(settings)
            with closing(sqlite3.connect(database.path)) as target:
                old.backup(target)
            database.initialize()
            database.initialize()
            with database.connect() as db:
                assert db.execute("PRAGMA user_version").fetchone()[0] == 3
                assert snapshot(db) == before, "Existing Phase 3 rows changed."
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert old.execute("PRAGMA user_version").fetchone()[0] == 2
        assert snapshot(old) == before
    print(
        json.dumps(
            {
                "result": "passed",
                "source_retained_v2": True,
                "copy_migrated_twice": True,
                "tables": before,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
