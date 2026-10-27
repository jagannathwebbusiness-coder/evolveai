"""Real model-API business generation + execution + restart reuse acceptance.

Explicitly enables unsecured educational host subprocesses in NEW isolated data.
No model/source fixtures, no downloads, no reset of application data.
"""

import argparse
import json
import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app
from app.improvement.business_contracts import BY_NAME

MISSING = ("business_break_even", "business_roi", "business_selling_price")
SCENARIO = (
    "A small business sells 200 units monthly at Rs600 each. Variable cost is Rs300 per unit, "
    "and monthly fixed costs are Rs45,000. Analyze revenue, total costs, profit, profit margin, "
    "break-even units, break-even revenue, and profit if sales increase by 20%. "
    "Use actual tools and verify all calculations. Recommend three practical actions to improve profitability."
)


def run_acceptance(names, data_dir, report_path, scenario=False):
    report = {
        "real_model_api": True,
        "model": "qwen2.5:3b",
        "secure_sandbox": False,
        "data_dir": str(data_dir),
        "capabilities": [],
        "acceptance_passed": False,
        "implementation_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "backend/app/agent/core.py",
                "backend/app/agent/policy.py",
                "backend/app/improvement/business_contracts.py",
                "backend/app/improvement/demo_builder.py",
                "backend/app/improvement/source_formatting.py",
                "backend/app/improvement/validation.py",
                "backend/app/tools/registry.py",
                "backend/app/improvement/demo_runner.py",
            )
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8"
        )

    save()
    try:
        for name in names:
            capability = BY_NAME[name]
            capability_dir = data_dir / name
            settings = Settings(
                # Reads LLM_API_KEY from the root .env or environment.
                demo_mode=True,
                evolve_data_dir=capability_dir,
                evolve_workspace_dir=capability_dir / "workspace",
                agent_timeout_seconds=600,
        llm_timeout_seconds=120,
            )
            record = {"name": name, "contract": capability.contract, "tasks": []}
            report["capabilities"].append(record)
            conversation_id = None
            active_version = None
            for index, operands in enumerate(capability.samples[:2]):
                # A completely new app instance reloads SQLite/registry for task 2.
                with TestClient(create_app(settings)) as client:
                    status = client.get("/model-status").json()
                    record["model_status"] = status
                    assert status["status"] == "ready", status
                    goal = (
                        "Calculate "
                        + name.removeprefix("business_").replace("_", " ")
                        + " using "
                        + json.dumps(operands)
                        + "."
                    )
                    print(f"REAL {name} task {index + 1}: {goal}", flush=True)
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
                    record["tasks"].append(
                        {
                            "goal": goal,
                            "expected": capability.oracle(operands),
                            "http_status": response.status_code,
                            "response": value,
                            "fresh_app_instance": True,
                        }
                    )
                    save()
                    assert response.status_code == 200, value
                    activity = value["activity"]
                    kinds = [e["kind"] for e in activity]
                    assert all(
                        k in kinds
                        for k in (
                            "plan_created",
                            "tool_executed",
                            "result_verified",
                            "goal_verified",
                        )
                    )
                    generated = [
                        e
                        for e in activity
                        if e["kind"] == "tool_executed"
                        and e["tool"].startswith("candidate_")
                    ]
                    assert len(generated) == 1
                    assert capability.matches(
                        generated[0]["result"]["data"], capability.oracle(operands)
                    )
                    snapshot = client.get("/improvement").json()
                    assert len(snapshot["active"]) == 1
                    if index == 0:
                        assert all(
                            k in kinds
                            for k in (
                                "tool_failed",
                                "capability_gap",
                                "candidate_generation",
                                "candidate_tests",
                                "candidate_activated",
                                "retry",
                            )
                        )
                        failure = next(
                            e
                            for e in activity
                            if e["kind"] == "tool_failed" and e["tool"] == name
                        )
                        assert failure["result"]["error"]["code"] == "unknown_tool"
                        run = next(
                            e["result"]
                            for e in activity
                            if e["kind"] == "candidate_tests"
                            and e["result"]["state"] == "passed"
                        )
                        assert len(run["payload"]["cases"]) == len(capability.cases())
                        assert all(c["passed"] for c in run["payload"]["cases"])
                        execution = run["payload"]["execution"]
                        assert (
                            execution["exit_code"] == 0 and execution["child_pid"] > 0
                        )
                        assert (
                            not execution["timeout"] and not execution["output_limit"]
                        )
                        active_version = snapshot["active"][0]["version_id"]
                        record["candidate"] = client.get(
                            f"/improvement/candidates/{snapshot['candidates'][0]['id']}"
                        ).json()
                    else:
                        assert (
                            "tool_reused" in kinds
                            and "candidate_generation" not in kinds
                        )
                        assert snapshot["active"][0]["version_id"] == active_version
                    assert all(
                        e["outcome"] == "verified_success"
                        for e in snapshot["evaluations"]
                    )
                    record["improvement"] = snapshot
                    conversation_id = value["conversation_id"]
                    save()
                    print(
                        f"PASS: {value['message']} ({value['duration_ms']} ms)",
                        flush=True,
                    )
            if scenario and name == "business_break_even":
                with TestClient(create_app(settings)) as client:
                    response = client.post(
                        "/chat",
                        json={
                            "messages": [{"role": "user", "content": SCENARIO}],
                            "use_tools": True,
                            "agentic": True,
                            "demo_tools": True,
                        },
                    )
                    value = response.json()
                    record["scenario"] = {
                        "goal": SCENARIO,
                        "http_status": response.status_code,
                        "response": value,
                    }
                    save()
                    assert response.status_code == 200, value
                    verified = [
                        e for e in value["activity"] if e["kind"] == "result_verified"
                    ]
                    assert len(verified) == 5
                    assert "projected_profit: 27000" in value["message"]
                    assert "candidate_generation" not in [
                        e["kind"] for e in value["activity"]
                    ]
                    assert "final_text_filtered" in [
                        e["kind"] for e in value["activity"]
                    ]
                    assert "Model interpretation" not in value["message"]
                    assert (
                        "Model recommendations (not independently verified):"
                        in value["message"]
                    )
                    recommendations = value["message"].split(
                        "Model recommendations (not independently verified):", 1
                    )[1]
                    from app.agent.policy import TaskPolicy
                    from app.schemas import ChatRequest

                    policy = TaskPolicy(
                        SCENARIO,
                        ChatRequest(
                            messages=[{"role": "user", "content": SCENARIO}],
                            use_tools=True,
                            agentic=True,
                        ),
                    )
                    assert len(policy.recommendations(recommendations)) == 3
                    print("PASS: original inline business scenario", flush=True)
            record["passed"] = True
        report["acceptance_passed"] = True
        save()
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {str(exc)[:2500]}"
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also generate/reuse ROI and selling-price tools.",
    )
    parser.add_argument(
        "--scenario",
        action="store_true",
        help="Also verify the original unit-economics scenario.",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data" / "business-demo" / str(uuid4())
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "docs" / "business-acceptance-results.json",
    )
    args = parser.parse_args()
    run_acceptance(
        MISSING if args.all else MISSING[:1], args.data_dir, args.report, args.scenario
    )
    print(f"Actual evidence: {args.report}", flush=True)


if __name__ == "__main__":
    main()
