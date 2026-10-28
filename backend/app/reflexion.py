"""Reflexion memory: short lessons from earlier tasks, stored in SQLite and recalled when planning.

Lessons are derived from recorded events only (no extra model call), so they cost no tokens and
cannot be invented. They are advice to the planner, never permission or verified fact.
"""

import json
import re
from uuid import uuid4

from .storage import now

STOP = {
    "this", "that", "with", "from", "have", "what", "which", "your", "about", "tool",
    "please", "create", "calculate", "using", "into", "when", "will", "make", "need",
}


def keywords(text, limit=12):
    words = []
    for word in re.findall(r"[a-z]{4,}", (text or "").lower()):
        if word not in STOP and word not in words:
            words.append(word)
    return words[:limit]


def lessons_from(events, goal):
    """Turn one task's recorded events into at most three short lessons."""
    found = []
    for event in events:
        kind, result = event.get("kind"), event.get("result") or {}
        if kind == "tool_failed" and isinstance(result.get("error"), dict):
            error = result["error"]
            found.append(
                (
                    result.get("category") or "tool_failure",
                    f"{event.get('tool') or 'A tool'} failed with {error.get('code')}: "
                    f"{str(error.get('message'))[:140]} Check arguments against the schema first.",
                )
            )
        elif kind == "capability_gap":
            found.append(
                (
                    "capability_gap",
                    f"No existing tool covered: {goal[:100]}. Building and testing a new tool worked "
                    "better than guessing.",
                )
            )
        elif kind == "recovery_succeeded":
            found.append(
                (
                    "recovery",
                    f"A failure on {event.get('tool') or 'a tool'} was fixed by changing the action, "
                    "not by repeating it.",
                )
            )
        elif kind == "failure":
            found.append(("task_failure", f"Task ended in failure: {event.get('message', '')[:140]}"))
    unique, seen = [], set()
    for category, lesson in found:
        if lesson not in seen:
            seen.add(lesson)
            unique.append((category, lesson))
    return unique[:3]


def save(database, task_id, goal):
    """Persist lessons for a finished task; returns how many were new."""
    with database.connect() as db:
        rows = db.execute(
            "SELECT payload FROM events WHERE task_id=? ORDER BY sequence", (str(task_id),)
        ).fetchall()
        events = [json.loads(row[0]) for row in rows]
        saved = 0
        for category, lesson in lessons_from(events, goal):
            if db.execute("SELECT 1 FROM lessons WHERE lesson=?", (lesson,)).fetchone():
                continue
            db.execute(
                "INSERT INTO lessons VALUES(?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    str(task_id),
                    category,
                    lesson,
                    " ".join(keywords(goal)),
                    now(),
                ),
            )
            saved += 1
    return saved


def recall(database, goal, limit=3):
    """Lessons whose keywords overlap the new goal, best overlap first."""
    wanted = set(keywords(goal))
    with database.connect() as db:
        rows = db.execute(
            "SELECT category,lesson,keywords FROM lessons ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    scored = []
    for category, lesson, words in rows:
        overlap = len(wanted & set(words.split()))
        if overlap:
            scored.append((overlap, category, lesson))
    scored.sort(key=lambda item: -item[0])
    return [{"category": c, "lesson": l} for _, c, l in scored[:limit]]


def count(database):
    with database.connect() as db:
        return db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
