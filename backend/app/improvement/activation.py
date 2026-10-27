"""Exact-artifact human decisions and atomic registry pointer changes."""

import json
import secrets
from uuid import uuid4

from ..llm_client import LLMError
from ..storage import Conflict, now
from .models import Binding, BUILTINS

ALLOWED_ORIGINS = {
    f"http://{host}:{port}"
    for host in ("127.0.0.1", "localhost")
    for port in (4173, 5173, 8000)
}


def review_origin(request):
    if (
        request.headers.get("origin") not in ALLOWED_ORIGINS
        or request.headers.get("x-evolve-review") != "human-review-v1"
        or request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}
    ):
        raise LLMError(
            403,
            "human_review_required",
            "Use the local Self-Development review page or an explicit local review session. Cross-origin/model tool requests cannot authorize changes.",
        )


def authorize(request):
    review_origin(request)
    supplied = request.headers.get("x-evolve-review-token", "")
    if not supplied or not secrets.compare_digest(
        supplied, request.app.state.review_token
    ):
        raise LLMError(
            403,
            "review_session_required",
            "Start a local human review session again; its capability expires when the backend restarts.",
        )
    return "local-human"


class Activation:
    def __init__(self, repository, sandbox):
        self.repository, self.sandbox = repository, sandbox

    def tested(self, version, db):
        contract = db.execute(
            "SELECT suite_hash FROM contracts WHERE version_id=?", (version["id"],)
        ).fetchone()
        run = db.execute(
            "SELECT * FROM validation_runs WHERE version_id=? ORDER BY created_at DESC LIMIT 1",
            (version["id"],),
        ).fetchone()
        if (
            not contract
            or not run
            or run["state"] != "passed"
            or run["source_hash"] != version["source_hash"]
            or run["content_hash"] != version["content_hash"]
            or run["suite_hash"] != contract[0]
        ):
            raise Conflict(
                "Exact version must pass its independent acceptance contract before approval or activation."
            )
        status = self.sandbox.status()
        payload = json.loads(run["payload"])
        if not status["available"] or any(
            payload["sandbox"].get(k) != status.get(k) for k in ("policy", "image")
        ):
            raise Conflict(
                "The tested sandbox/image is unavailable or changed. Retest in the configured sandbox."
            )
        return payload

    def approved(self, version, db):
        decision = db.execute(
            "SELECT action,source_hash,content_hash FROM decisions WHERE version_id=? AND action IN ('approve','reject','cancel') ORDER BY created_at DESC LIMIT 1",
            (version["id"],),
        ).fetchone()
        if not decision or tuple(decision) != (
            "approve",
            version["source_hash"],
            version["content_hash"],
        ):
            raise Conflict(
                "Explicit approval under the selected execution policy is required for this exact version and hashes."
            )

    def decide(self, body, actor):
        with self.repository.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = self.repository.bound(
                body, db, latest=body.action not in {"rollback", "deactivate"}
            )
            action, state = body.action, version["status"]
            manifest = version["manifest"]
            name = manifest["name"] if manifest else ""
            current = db.execute(
                "SELECT version_id FROM active_tools WHERE name=?", (name,)
            ).fetchone()
            before = current[0] if current else None
            after = before
            if action == "approve":
                if state != "awaiting_approval":
                    raise Conflict(
                        "Only successfully tested candidates awaiting approval can be approved."
                    )
                self.tested(version, db)
                new_state = "approved"
            elif action in {"reject", "cancel"}:
                if state in {
                    "active",
                    "inactive",
                    "superseded",
                    "rejected",
                    "cancelled",
                    "proposed",
                    "validating",
                }:
                    raise Conflict(
                        "This lifecycle state cannot be rejected or cancelled. Deactivate an active version first."
                    )
                new_state = "rejected" if action == "reject" else "cancelled"
            elif action == "deactivate":
                if before != version["id"]:
                    raise Conflict(
                        "Only the currently active version can be deactivated."
                    )
                db.execute("DELETE FROM active_tools WHERE name=?", (name,))
                new_state, after = "inactive", None
            else:
                if (
                    not manifest
                    or name in BUILTINS
                    or state not in {"approved", "inactive"}
                ):
                    raise Conflict(
                        "Only approved or previously active inactive generated versions can be activated."
                    )
                payload = self.tested(version, db)
                self.approved(version, db)
                if action == "rollback":
                    if (
                        not before
                        or before == version["id"]
                        or not db.execute(
                            "SELECT 1 FROM activation_audit WHERE name=? AND after_id=?",
                            (name, version["id"]),
                        ).fetchone()
                    ):
                        raise Conflict(
                            "Rollback requires a different previously active approved version."
                        )
                else:
                    prior = db.execute(
                        "SELECT after_id FROM activation_audit WHERE name=? AND after_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                        (name,),
                    ).fetchone()
                    baseline_id = before or (prior[0] if prior else None)
                    comparison = payload.get("comparison") or {}
                    if (
                        baseline_id
                        and baseline_id != version["id"]
                        and (
                            manifest.get("replaces") != name
                            or not comparison.get("improved")
                            or comparison.get("baseline_version_id") != baseline_id
                        )
                    ):
                        raise Conflict(
                            "Replacement needs regression-preserving measurable improvement against the currently active baseline."
                        )
                # One transaction includes state, pointer and audit; any failure rolls all back.
                db.execute(
                    "INSERT INTO active_tools VALUES(?,?) ON CONFLICT(name) DO UPDATE SET version_id=excluded.version_id",
                    (name, version["id"]),
                )
                if before:
                    db.execute(
                        "UPDATE candidate_states SET status='inactive' WHERE version_id=?",
                        (before,),
                    )
                after, new_state = version["id"], "active"
            db.execute(
                "UPDATE candidate_states SET status=?,error=NULL WHERE version_id=?",
                (new_state, version["id"]),
            )
            db.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    version["id"],
                    version["source_hash"],
                    version["content_hash"],
                    action,
                    body.reason,
                    actor,
                    now(),
                ),
            )
            if action in {"activate", "deactivate", "rollback"}:
                db.execute(
                    "INSERT INTO activation_audit VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        name,
                        before,
                        after,
                        action,
                        actor,
                        body.reason,
                        now(),
                    ),
                )
        return self.repository.detail(version["candidate_id"])

    def active(self):
        with self.repository.database.connect() as db:
            return [
                self.repository.version(r[0], db)
                for r in db.execute(
                    "SELECT version_id FROM active_tools ORDER BY name"
                ).fetchall()
            ]

    def executable(self, identifier):
        with self.repository.database.connect() as db:
            version = self.repository.version(identifier, db)
            binding = Binding(
                version_id=identifier,
                source_hash=version["source_hash"],
                content_hash=version["content_hash"],
            )
            self.repository.bound(binding, db, latest=False)
            if (
                version["status"] != "active"
                or not db.execute(
                    "SELECT 1 FROM active_tools WHERE version_id=?", (str(identifier),)
                ).fetchone()
            ):
                raise Conflict("Generated tool is no longer active.")
            self.tested(version, db)
            self.approved(version, db)
        return version
