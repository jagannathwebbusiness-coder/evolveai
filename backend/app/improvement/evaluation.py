"""Deterministic, redacted metrics from actual tasks. Model confidence is not evidence."""

import json
import re
from uuid import uuid4

from ..storage import encode, now
from ..tools.calculator import CalculatorArgs, calculate
from ..tools.errors import ToolError
from .models import BUILTINS, digest
from .business_contracts import BY_NAME, goal_requirements, render_results

ERRORS = {
    "unknown_tool",
    "invalid_tool_arguments",
    "upload_not_attached",
    "division_by_zero",
    "llm_timeout",
    "llm_unreachable",
    "model_missing",
    "tool_limit",
    "iteration_limit",
    "invalid_tool_calls",
    "required_tool_missing",
    "repeated_tool_call",
    "agent_timeout",
    "invalid_tool_response",
    "required_tool_not_used",
    "truncated_tool_call",
    "tool_call_limit",
    "workspace_error",
    "invalid_llm_response",
    "llm_error",
    "unreachable",
    "error",
    "invalid_business_input",
    "numeric_overflow",
    "invalid_csv_size",
    "invalid_csv_encoding",
    "invalid_csv_content",
    "invalid_csv_header",
    "malformed_csv",
    "csv_row_limit",
    "empty_csv",
    "invalid_columns",
    "unsafe_upload_path",
    "upload_not_found",
    "file_wrong_context",
    "file_not_registered",
    "unsafe_workspace_path",
    "workspace_file_missing",
    "unsupported_file_type",
    "workspace_file_too_large",
    "invalid_text_file",
    "invalid_directory",
    "conversation_required",
    "report_too_large",
    "invalid_report",
    "invalid_report_json",
    "report_storage_limit",
    "candidate_execution_failed",
    "irrelevant_tool",
    "ungrounded_file",
    "ungrounded_arguments",
    "prerequisite_missing",
    "candidate_build_failed",
    "capability_blocked",
    "recovery_limit",
}


def category(code):
    return code if code in ERRORS else "execution_failure"


def capability(name):
    return (
        name
        if name in BUILTINS
        or name in BY_NAME
        or (
            isinstance(name, str)
            and re.fullmatch(r"candidate_[a-z][a-z0-9_]{0,45}", name)
        )
        else "unavailable_capability"
    )


class Evaluator:
    def __init__(self, database):
        self.database = database

    def record_request(self, task_id, body):
        data = {
            "task_type": "tools" if body.use_tools else "chat",
            "requested_capability": body.tool_choice if body.use_tools else "chat",
            "retry_of": str(body.retry_of) if body.retry_of else None,
        }
        if body.retry_of:
            parent = self.database.task(body.retry_of)
            current = self.database.task(task_id)
            if (
                parent["conversation_id"] != current["conversation_id"]
                or parent["status"] == "running"
            ):
                from ..storage import Conflict

                raise Conflict(
                    "Retry must reference a finished task in this conversation."
                )
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO task_evidence VALUES(?,?)", (str(task_id), encode(data))
            )

    def record_error(self, task_id, code):
        with self.database.connect() as db:
            row = db.execute(
                "SELECT payload FROM task_evidence WHERE task_id=?", (str(task_id),)
            ).fetchone()
            data = json.loads(row[0]) if row else {}
            data["error_category"] = category(code)
            db.execute(
                "INSERT OR REPLACE INTO task_evidence VALUES(?,?)",
                (str(task_id), encode(data)),
            )

    def refresh(self):
        with self.database.connect() as db:
            identifiers = [
                r[0] for r in db.execute("SELECT id FROM tasks WHERE status!='running'")
            ]
        for identifier in identifiers:
            task = self.database.task(identifier)
            with self.database.connect() as db:
                row = db.execute(
                    "SELECT payload FROM task_evidence WHERE task_id=?", (identifier,)
                ).fetchone()
                evidence = json.loads(row[0]) if row else {}
                final = db.execute(
                    "SELECT content FROM messages WHERE task_id=? AND role='assistant' ORDER BY seq DESC LIMIT 1",
                    (identifier,),
                ).fetchone()
                feedback = db.execute(
                    "SELECT rating FROM feedback WHERE task_id=?", (identifier,)
                ).fetchone()
                retry_count, cursor, visited = 0, identifier, set()
                while cursor and cursor not in visited:
                    visited.add(cursor)
                    entry = db.execute(
                        "SELECT payload FROM task_evidence WHERE task_id=?", (cursor,)
                    ).fetchone()
                    cursor = json.loads(entry[0]).get("retry_of") if entry else None
                    if cursor:
                        retry_count += 1
            events = task["activity"]
            calls = [
                e
                for e in events
                if e["kind"] in {"tool_requested", "tool_executed", "tool_failed"}
            ]
            failed = task["status"] not in {"completed", "completed_recovered"}
            error = evidence.get("error_category")
            if failed and not error:
                error = category(
                    next(
                        (
                            e.get("result", {}).get("error", {}).get("code")
                            for e in events
                            if e["kind"] == "tool_failed"
                            and isinstance(e.get("result"), dict)
                        ),
                        "execution_failure",
                    )
                )
            verification = []
            numeric_final_verified = False
            median_final_verified = False
            successful = [e for e in events if e["kind"] == "tool_executed"]
            for event in successful:
                if event.get("tool") == "calculate":
                    try:
                        expected = calculate(
                            CalculatorArgs.model_validate(event["arguments"])
                        )
                        correct = event["result"].get("ok") is True and digest(
                            expected
                        ) == digest(event["result"].get("data"))
                        verification.append(
                            {
                                "event_sequence": event["sequence"],
                                "rule": "calculator-recompute-v1",
                                "passed": correct,
                                "scope": "tool_result",
                            }
                        )
                        if (
                            correct
                            and len(successful) == 1
                            and final
                            and re.fullmatch(
                                r"\s*[-+]?\d+(?:\.\d+)?\s*(?:%|percent)?\s*\.?\s*",
                                final[0],
                            )
                        ):
                            number = re.search(r"[-+]?\d+(?:\.\d+)?", final[0])[0]
                            suffix_ok = expected["unit"] == "percent" or not re.search(
                                r"%|percent", final[0]
                            )
                            numeric_final_verified = (
                                suffix_ok and float(number) == expected["value"]
                            )
                    except (ValueError, TypeError, KeyError, ToolError):
                        verification.append(
                            {
                                "event_sequence": event["sequence"],
                                "rule": "calculator-recompute-v1",
                                "passed": False,
                                "scope": "tool_result",
                            }
                        )
            if any(not item["passed"] for item in verification):
                failed, error = True, "verification_failure"
            if any(e["kind"] == "goal_verified" for e in events):
                with self.database.connect() as db:
                    user = db.execute(
                        "SELECT content FROM messages WHERE task_id=? AND role='user' ORDER BY seq DESC LIMIT 1",
                        (identifier,),
                    ).fetchone()
                    tool_contracts = {
                        r[0]: r[1]
                        for r in db.execute(
                            "SELECT DISTINCT json_extract(manifest,'$.name'),json_extract(manifest,'$.demo_contract') FROM candidate_versions WHERE manifest IS NOT NULL"
                        )
                    }
                requirements = goal_requirements(user[0]) if user else []
                independently_verified = []
                done = set()
                for event in successful:
                    for item in requirements:
                        if item["label"] in done:
                            continue
                        c = item["capability"]
                        data = event.get("result", {}).get("data")
                        actual = None
                        if event.get("result", {}).get("ok") is True:
                            if tool_contracts.get(
                                event.get("tool")
                            ) == c.contract and c.arguments_match(
                                event.get("arguments"), item["arguments"]
                            ):
                                actual = data
                            elif (
                                event.get("tool") == "calculate"
                                and c.builtin
                                and event.get("arguments")
                                == c.builtin_arguments(item["arguments"])
                            ):
                                actual = c.from_builtin(data)
                        if actual is not None:
                            try:
                                passed = c.matches(actual, c.oracle(item["arguments"]))
                            except (ValueError, TypeError, KeyError):
                                passed = False
                            verification.append(
                                {
                                    "rule": c.contract,
                                    "passed": passed,
                                    "scope": "grounded_numeric_result",
                                    "event_sequence": event["sequence"],
                                }
                            )
                            if passed:
                                independently_verified.append(item | {"result": actual})
                                done.add(item["label"])
                median_final_verified = bool(
                    requirements
                    and len(done) == len(requirements)
                    and final
                    and final[0].startswith(render_results(independently_verified))
                )
                verification.append(
                    {
                        "rule": "required-contract-results-v2",
                        "passed": median_final_verified,
                        "scope": "required_numeric_results_and_rendered_values; prose_not_verified",
                    }
                )
                if not median_final_verified:
                    failed, error = True, "verification_failure"
            outcome = (
                "failure"
                if failed or not final
                else (
                    "verified_success"
                    if numeric_final_verified or median_final_verified
                    else "unverified_completion"
                )
            )
            value = {
                "task_id": identifier,
                "rules_version": "deterministic-v1",
                "task_type": evidence.get("task_type", "tools" if calls else "chat"),
                "requested_capability": evidence.get(
                    "requested_capability", "unknown_legacy"
                ),
                "status": task["status"],
                "outcome": outcome,
                "tool_calls": sum(e["kind"] == "tool_requested" for e in events),
                "tools_executed": len(successful),
                "validation_failures": int(
                    error
                    in {
                        "invalid_tool_arguments",
                        "invalid_tool_calls",
                        "unknown_tool",
                        "upload_not_attached",
                    }
                ),
                "retry_count": retry_count if row else None,
                "recovery_attempts": sum(e["kind"] == "tool_failed" for e in events),
                "generated_candidates": sum(
                    e["kind"] == "candidate_generation" for e in events
                ),
                "activated_candidates": sum(
                    e["kind"] == "candidate_activated" for e in events
                ),
                "reused_tools": sum(e["kind"] == "tool_reused" for e in events),
                "retry_of": evidence.get("retry_of"),
                "error_category": error,
                "duration_ms": task["duration_ms"],
                "final_answer_produced": bool(final),
                "verification": verification,
                "feedback": feedback[0] if feedback else None,
                "event_references": [
                    {
                        "sequence": e["sequence"],
                        "kind": e["kind"],
                        "tool": capability(e["tool"]) if e.get("tool") else None,
                    }
                    for e in events
                ],
                "created_at": task["created_at"],
            }
            with self.database.connect() as db:
                db.execute(
                    "INSERT INTO evaluations VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                    (identifier, encode(value), now()),
                )
        self.gaps()

    def gaps(self):
        groups = {}
        with self.database.connect() as db:
            for row in db.execute("SELECT payload FROM evaluations"):
                value = json.loads(row[0])
                if value["outcome"] != "failure" and value["feedback"] != "unhelpful":
                    continue
                cap = value["requested_capability"]
                code = value["error_category"] or "user_reported_unhelpful"
                key = f"{cap}:{code}"
                groups.setdefault(key, []).append(
                    {
                        "task_id": value["task_id"],
                        "events": value["event_references"],
                        "category": code,
                    }
                )
            # Old gaps remain auditable when their source tasks are explicitly deleted.
            for row in db.execute("SELECT id,payload FROM gaps").fetchall():
                prior = json.loads(row["payload"])
                if prior.get("capability") in BY_NAME:
                    retained = [
                        e
                        for e in prior.get("evidence", [])
                        if db.execute(
                            "SELECT 1 FROM tasks WHERE id=?", (e["task_id"],)
                        ).fetchone()
                    ]
                    resolved = sum(
                        bool(
                            db.execute(
                                "SELECT 1 FROM events WHERE task_id=? AND json_extract(payload,'$.kind')='goal_verified'",
                                (e["task_id"],),
                            ).fetchone()
                        )
                        for e in retained
                    )
                    prior.update(
                        evidence=retained,
                        occurrences=len(retained),
                        resolved_tasks=resolved,
                        status=(
                            "resolved_demo_gap"
                            if resolved
                            else (
                                "observed_demo_gap"
                                if retained
                                else "no_current_evidence"
                            )
                        ),
                    )
                    db.execute(
                        "UPDATE gaps SET payload=? WHERE id=?",
                        (encode(prior), row["id"]),
                    )
                    continue
                prior.update(status="no_current_evidence", occurrences=0, evidence=[])
                db.execute(
                    "UPDATE gaps SET payload=? WHERE id=?", (encode(prior), row["id"])
                )
            for fingerprint, evidence in groups.items():
                cap, code = fingerprint.split(":", 1)
                existing = db.execute(
                    "SELECT id FROM gaps WHERE fingerprint=?", (fingerprint,)
                ).fetchone()
                identifier = existing[0] if existing else str(uuid4())
                status = (
                    "confirmed"
                    if len(evidence) >= self.database.settings.improvement_min_evidence
                    else "tentative"
                )
                data = {
                    "id": identifier,
                    "capability": cap,
                    "error_category": code,
                    "occurrences": len(evidence),
                    "minimum_evidence": self.database.settings.improvement_min_evidence,
                    "status": status,
                    "evidence": evidence,
                    "proposed_improvement": "Investigate this failure category; propose a bounded pure JSON tool only if a deterministic acceptance contract can address it.",
                    "expected_benefit": "Fewer failures on the same human-reviewed regression cases. No improvement demonstrated yet.",
                    "risk": "high_untrusted_code",
                }
                db.execute(
                    "INSERT INTO gaps VALUES(?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET payload=excluded.payload",
                    (identifier, fingerprint, encode(data), now()),
                )
