r"""Real local inference + actual candidate subprocess demo. No transport fixtures.

Run from repo root: .\backend\.venv\Scripts\python.exe backend/demo_agentic.py
This command explicitly opts into unsecured educational host Python execution.
Uses its own timestamped database/workspace; preserves the application's data.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data" / "agentic-demo" / str(uuid4())
    )
    parser.add_argument(
        "--report", type=Path, default=ROOT / "docs" / "agentic-demo-results.json"
    )
    args = parser.parse_args()
    settings = Settings(
        # Reads LLM_API_KEY from the root .env or environment.
        demo_mode=True,
        evolve_data_dir=args.data_dir,
        evolve_workspace_dir=args.data_dir / "workspace",
        agent_timeout_seconds=600,
        llm_timeout_seconds=120,
    )
    report = {
        "model": settings.llm_model,
        "data_dir": str(settings.evolve_data_dir),
        "real_model_api": True,
        "secure_sandbox": False,
        "tasks": [],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        status = client.get("/model-status").json()
        report["model_status"] = status
        save()
        assert status["status"] == "ready", status
        goals = [
            (
                "Find the median of [9, 1, 4, 7]. Use the median capability and verify the result.",
                "The median is 5.5.",
            ),
            (
                "Find the median of [18, 2, 11, 6, 9]. Use the median capability and verify the result.",
                "The median is 9.",
            ),
        ]
        conversation_id = None
        for index, (goal, expected) in enumerate(goals):
            print(f"Real demo task {index + 1}: {goal}", flush=True)
            response = client.post(
                "/chat",
                json={
                    "messages": [{"role": "user", "content": goal}],
                    "use_tools": True,
                    "agentic": True,
                    "demo_tools": True,
                    "conversation_id": conversation_id,
                },
            )
            value = response.json()
            report["tasks"].append(
                {"goal": goal, "http_status": response.status_code, "response": value}
            )
            save()
            assert response.status_code == 200, value
            assert value["message"] == expected, value
            kinds = [e["kind"] for e in value["activity"]]
            assert (
                "plan_created" in kinds
                and "tool_executed" in kinds
                and "goal_verified" in kinds
            )
            if index == 0:
                assert all(
                    k in kinds
                    for k in (
                        "tool_failed",
                        "reflection",
                        "candidate_generation",
                        "candidate_tests",
                        "candidate_activated",
                        "retry",
                    )
                ), kinds
                tests = next(
                    e["result"]
                    for e in value["activity"]
                    if e["kind"] == "candidate_tests"
                    and e["result"]["state"] == "passed"
                )
                assert len(tests["payload"]["cases"]) == 17
                assert tests["payload"]["execution"]["exit_code"] == 0
                assert tests["payload"]["execution"]["child_pid"] > 0
                assert all(case["passed"] for case in tests["payload"]["cases"])
            else:
                assert (
                    "tool_reused" in kinds and "candidate_generation" not in kinds
                ), kinds
            conversation_id = value["conversation_id"]
            print(f"PASS: {value['message']} ({value['duration_ms']} ms)", flush=True)
        report["improvement"] = client.get("/improvement").json()
        report["acceptance_passed"] = True
        save()
        print(f"Actual evidence saved to {args.report}", flush=True)


if __name__ == "__main__":
    main()
