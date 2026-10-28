"""Evaluation metrics from the project deck, computed from recorded tasks and events only."""

import json


def ratio(part, whole):
    return round(part / whole, 3) if whole else None


def compute(database):
    with database.connect() as db:
        statuses = [r[0] for r in db.execute("SELECT status FROM tasks WHERE status!='running'")]
        durations = [r[0] for r in db.execute("SELECT duration_ms FROM tasks WHERE duration_ms IS NOT NULL")]
        events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events")]
        built = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        active = db.execute(
            "SELECT COUNT(*) FROM candidate_states WHERE status='active'"
        ).fetchone()[0]
        lessons = db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    kinds = [e.get("kind") for e in events]
    ok = sum(1 for s in statuses if s.startswith("completed"))
    ran = kinds.count("tool_executed")
    failed = kinds.count("tool_failed")
    generated_ran = sum(
        1 for e in events if e.get("kind") == "tool_executed" and str(e.get("tool", "")).startswith("candidate_")
    )
    generated_failed = sum(
        1 for e in events if e.get("kind") == "tool_failed" and str(e.get("tool", "")).startswith("candidate_")
    )
    return {
        "tasks": len(statuses),
        "task_success_rate": ratio(ok, len(statuses)),
        "recovered_tasks": statuses.count("completed_recovered"),
        "tool_accuracy": ratio(ran, ran + failed),
        "avg_execution_seconds": round(sum(durations) / len(durations) / 1000, 1) if durations else None,
        "human_intervention_rate": ratio(kinds.count("approval_required"), len(statuses)),
        "tools_built": built,
        "tools_active": active,
        "lessons_learned": lessons,
        "lessons_recalled": kinds.count("reflexion_recalled"),
        "regression_rate": ratio(generated_failed, generated_ran + generated_failed),
    }
