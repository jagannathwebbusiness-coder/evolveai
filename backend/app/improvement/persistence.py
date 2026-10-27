"""Schema v3 and candidate repository; source, runs and decisions are append-only."""

import json
from uuid import uuid4

from ..storage import Conflict, MissingRecord, encode, now
from .models import Artifact, digest


def migrate(db):
    statements = [
        "CREATE TABLE task_evidence(task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, payload TEXT NOT NULL)",
        "CREATE TABLE evaluations(task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, payload TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE feedback(task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful')), created_at TEXT NOT NULL)",
        "CREATE TABLE gaps(id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE candidates(id TEXT PRIMARY KEY, gap_id TEXT NOT NULL REFERENCES gaps(id), created_at TEXT NOT NULL)",
        "CREATE TABLE candidate_versions(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(id), version INTEGER NOT NULL, manifest TEXT, source TEXT NOT NULL, source_hash TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(candidate_id,version))",
        "CREATE TABLE candidate_states(version_id TEXT PRIMARY KEY REFERENCES candidate_versions(id), status TEXT NOT NULL, error TEXT)",
        "CREATE TABLE contracts(version_id TEXT PRIMARY KEY REFERENCES candidate_versions(id), cases TEXT NOT NULL, suite_hash TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE validation_runs(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES candidate_versions(id), state TEXT NOT NULL, source_hash TEXT NOT NULL, content_hash TEXT NOT NULL, suite_hash TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE decisions(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES candidate_versions(id), source_hash TEXT NOT NULL, content_hash TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE active_tools(name TEXT PRIMARY KEY, version_id TEXT UNIQUE NOT NULL REFERENCES candidate_versions(id))",
        "CREATE TABLE activation_audit(id TEXT PRIMARY KEY, name TEXT NOT NULL, before_id TEXT, after_id TEXT, action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE INDEX versions_candidate ON candidate_versions(candidate_id,version)",
        "CREATE INDEX runs_version ON validation_runs(version_id,created_at)",
    ]
    for sql in statements:
        db.execute(sql)
    for table in (
        "candidate_versions",
        "contracts",
        "validation_runs",
        "decisions",
        "activation_audit",
    ):
        for operation in ("UPDATE", "DELETE"):
            db.execute(
                f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable audit/source record'); END"
            )


class Repository:
    def __init__(self, database):
        self.database = database

    def proposed(self, gap_id):
        identifier, version_id, stamp = str(uuid4()), str(uuid4()), now()
        with self.database.connect() as db:
            if not db.execute(
                "SELECT 1 FROM gaps WHERE id=?", (str(gap_id),)
            ).fetchone():
                raise MissingRecord(
                    "Capability gap not found. Refresh task evidence first."
                )
            db.execute(
                "INSERT INTO candidates VALUES(?,?,?)", (identifier, str(gap_id), stamp)
            )
            db.execute(
                "INSERT INTO candidate_versions VALUES(?,?,1,NULL,'',?,?,?)",
                (version_id, identifier, digest(""), digest({}), stamp),
            )
            db.execute(
                "INSERT INTO candidate_states VALUES(?,'proposed',NULL)", (version_id,)
            )
        return identifier, version_id

    def append(self, candidate_id, artifact: Artifact, base_id):
        source = artifact.source
        manifest = artifact.manifest.model_dump(mode="json")
        identifier = str(uuid4())
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT id,version,manifest FROM candidate_versions WHERE candidate_id=? ORDER BY version DESC LIMIT 1",
                (str(candidate_id),),
            ).fetchone()
            if not previous or previous["id"] != str(base_id):
                raise Conflict(
                    "Candidate changed. Review the latest version before adding a revision."
                )
            if (
                previous["manifest"]
                and json.loads(previous["manifest"])["name"] != manifest["name"]
            ):
                raise Conflict(
                    "A candidate's tool name is immutable. Create a separate candidate for another name."
                )
            owner = db.execute(
                "SELECT candidate_id FROM candidate_versions WHERE json_extract(manifest,'$.name')=? LIMIT 1",
                (manifest["name"],),
            ).fetchone()
            if owner and owner[0] != str(candidate_id):
                raise Conflict("Tool name belongs to another candidate.")
            db.execute(
                "INSERT INTO candidate_versions VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    str(candidate_id),
                    previous["version"] + 1,
                    encode(manifest),
                    source,
                    digest(source),
                    digest({"manifest": manifest, "source": source}),
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO candidate_states VALUES(?,'generated',NULL)", (identifier,)
            )
            # Earlier approvals remain historical only; latest-version gates prevent reuse.
            db.execute(
                "UPDATE candidate_states SET status='superseded' WHERE version_id=? AND status NOT IN ('active','inactive','rejected','cancelled')",
                (str(base_id),),
            )
        return self.version(identifier)

    def state(self, version_id, status, error=None):
        with self.database.connect() as db:
            db.execute(
                "UPDATE candidate_states SET status=?,error=? WHERE version_id=?",
                (status, error, str(version_id)),
            )

    def generation_failure(self, candidate_id, proposed_id, raw, error):
        """Retain bounded raw model output as immutable review data, never executable code."""
        if not raw:
            self.state(proposed_id, "generation_failed", error)
            return
        source = "# Unvalidated generation response; never executable\n" + raw[:40000]
        identifier = str(uuid4())
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = (
                db.execute(
                    "SELECT MAX(version) FROM candidate_versions WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()[0]
                + 1
            )
            db.execute(
                "INSERT INTO candidate_versions VALUES(?,?,?,NULL,?,?,?,?)",
                (
                    identifier,
                    candidate_id,
                    version,
                    source,
                    digest(source),
                    digest({"manifest": None, "source": source}),
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO candidate_states VALUES(?,'generation_failed',?)",
                (identifier, error),
            )
            db.execute(
                "UPDATE candidate_states SET status='superseded' WHERE version_id=?",
                (proposed_id,),
            )

    def version(self, identifier, db=None):
        if db is None:
            with self.database.connect() as connection:
                return self.version(identifier, connection)
        row = db.execute(
            "SELECT v.*,s.status,s.error FROM candidate_versions v JOIN candidate_states s ON s.version_id=v.id WHERE v.id=?",
            (str(identifier),),
        ).fetchone()
        if not row:
            raise MissingRecord("Candidate version not found.")
        value = dict(row)
        value["manifest"] = json.loads(value["manifest"]) if value["manifest"] else None
        return value

    def bound(self, binding, db, latest=True):
        version = self.version(binding.version_id, db)
        if (
            version["source_hash"] != binding.source_hash
            or version["content_hash"] != binding.content_hash
            or digest(version["source"]) != version["source_hash"]
            or (
                version["manifest"]
                and digest(
                    {"manifest": version["manifest"], "source": version["source"]}
                )
                != version["content_hash"]
            )
        ):
            raise Conflict(
                "Source or manifest hash changed; tests and approval do not apply."
            )
        if (
            latest
            and db.execute(
                "SELECT id FROM candidate_versions WHERE candidate_id=? ORDER BY version DESC LIMIT 1",
                (version["candidate_id"],),
            ).fetchone()[0]
            != version["id"]
        ):
            raise Conflict(
                "This version was superseded. Test and review the latest version."
            )
        return version

    def detail(self, identifier):
        with self.database.connect() as db:
            row = db.execute(
                "SELECT * FROM candidates WHERE id=?", (str(identifier),)
            ).fetchone()
            if not row:
                raise MissingRecord("Candidate not found.")
            result = dict(row)
            result["versions"] = []
            for item in db.execute(
                "SELECT id FROM candidate_versions WHERE candidate_id=? ORDER BY version DESC",
                (str(identifier),),
            ).fetchall():
                version = self.version(item[0], db)
                version["runs"] = [
                    dict(r) | {"payload": json.loads(r["payload"])}
                    for r in db.execute(
                        "SELECT * FROM validation_runs WHERE version_id=? ORDER BY created_at DESC",
                        (item[0],),
                    )
                ]
                version["decisions"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM decisions WHERE version_id=? ORDER BY created_at DESC",
                        (item[0],),
                    )
                ]
                contract = db.execute(
                    "SELECT * FROM contracts WHERE version_id=?", (item[0],)
                ).fetchone()
                version["contract"] = (
                    dict(contract) | {"cases": json.loads(contract["cases"])}
                    if contract
                    else None
                )
                result["versions"].append(version)
            return result

    def save_run(self, version, state, suite_hash, payload):
        record = {
            "id": str(uuid4()),
            "version_id": version["id"],
            "state": state,
            "source_hash": version["source_hash"],
            "content_hash": version["content_hash"],
            "suite_hash": suite_hash,
            "payload": payload,
            "created_at": now(),
        }
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO validation_runs VALUES(?,?,?,?,?,?,?,?)",
                tuple(
                    encode(record[k]) if k == "payload" else record[k] for k in record
                ),
            )
        return record
