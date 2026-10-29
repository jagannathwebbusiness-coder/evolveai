"""Offline decision fixtures; candidate execution uses REAL local child processes.

These tests do not claim real-model success. backend/demo_agentic.py verifies that separately.
"""

import copy
import json
from uuid import uuid4

import httpx
from fakeapi import adapt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.improvement.activation import Activation
from app.improvement.demo_contracts import CONTRACT, DESCRIPTION, INPUT, OUTPUT, cases
from app.improvement.demo_subprocess import DemoSubprocess
from app.improvement.models import Artifact, Binding, ContractInput, DecisionInput
from app.improvement.persistence import Repository
from app.improvement.runtime import generated_specs
from app.improvement.sandbox import SandboxError
from app.improvement.validation import Validator
from app.storage import Conflict, Database, encode, now

SOURCE = """def run(arguments):
    values = sorted(arguments["numbers"])
    size = len(values)
    middle = size // 2
    if size % 2:
        return {"median": float(values[middle])}
    return {"median": (values[middle-1] + values[middle]) / 2.0}
"""
GOAL = (
    "Find the median of [9, 1, 4, 7]. Use the median capability and verify the result."
)
PLAN = {
    "steps": ["Request median", "Inspect result"],
    "success_check": "Exact median result",
}


def action(
    tool="median_numbers", arguments=None, kind="tool", summary="Compute median"
):
    return {
        "kind": kind,
        "step": 1,
        "tool": tool,
        "arguments": arguments if arguments is not None else {"numbers": [9, 1, 4, 7]},
        "summary": summary,
    }


def draft(source=SOURCE):
    return {"source": source}


def transport(sequence):
    queue = list(sequence)

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
        assert queue, "Unexpected model request"
        value = queue.pop(0)
        if "kind" in value:
            message = {
                "role": "assistant",
                "content": value["summary"] if value["kind"] != "tool" else "",
            }
            if value["kind"] == "tool":
                message["tool_calls"] = [
                    {
                        "function": {
                            "name": value["tool"],
                            "arguments": value["arguments"],
                        }
                    }
                ]
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5:3b",
                    "done": True,
                    "done_reason": "stop",
                    "message": message,
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(value)},
            },
        )

    return adapt(handler)


def request(client, goal=GOAL, **changes):
    payload = {
        "messages": [{"role": "user", "content": goal}],
        "use_tools": True,
        "agentic": True,
        "demo_tools": True,
    }
    payload.update(changes)
    return client.post("/chat", json=payload)


def artifact(source=SOURCE, **overrides):
    manifest = dict(
        name="candidate_median_test",
        description=DESCRIPTION,
        demo_contract=CONTRACT,
        input_schema=INPUT,
        output_schema=OUTPUT,
        generated_tests=[c.model_dump() for c in cases()[:2]],
    )
    manifest.update(overrides)
    return Artifact(source=source, manifest=manifest)


def bind(version):
    return {
        "version_id": version["id"],
        "source_hash": version["source_hash"],
        "content_hash": version["content_hash"],
    }


def prepared(settings):
    database = Database(settings)
    database.initialize()
    repository = Repository(database)
    gap_id = str(uuid4())
    with database.connect() as db:
        db.execute(
            "INSERT INTO gaps VALUES(?,?,?,?)",
            (gap_id, gap_id, encode({"occurrences": 1}), now()),
        )
    cid, proposed = repository.proposed(gap_id)
    version = repository.append(cid, artifact(), proposed)
    return database, repository, version


def test_real_child_generation_activation_reuse_and_evidence(tmp_path):
    settings = Settings(_env_file=None, demo_mode=True)
    sequence = [
        PLAN,
        action(),
        draft(),
        action(),
        PLAN,
        action(),
    ]
    with TestClient(create_app(settings, transport(sequence))) as client:
        first = request(client)
        assert first.status_code == 200, first.text
        first = first.json()
        assert first["message"] == "The median is 5.5."
        kinds = [e["kind"] for e in first["activity"]]
        assert (
            kinds.index("tool_failed")
            < kinds.index("candidate_generation")
            < kinds.index("candidate_tests")
            < kinds.index("candidate_activated")
            < kinds.index("retry")
            < kinds.index("tool_executed")
        )
        run = next(
            e["result"] for e in first["activity"] if e["kind"] == "candidate_tests"
        )
        assert run["state"] == "passed" and len(run["payload"]["cases"]) == 17
        assert run["payload"]["execution"]["child_pid"] > 0
        assert run["payload"]["execution"]["secure_sandbox"] is False
        assert (
            client.get(f"/tasks/{first['task_id']}").json()["status"]
            == "completed_recovered"
        )
        second = request(client, conversation_id=first["conversation_id"])
        assert second.status_code == 200, second.text
        kinds = [e["kind"] for e in second.json()["activity"]]
        assert "tool_reused" in kinds and "candidate_generation" not in kinds
        evidence = next(
            e["result"]
            for e in second.json()["activity"]
            if e["kind"] == "strategy_evidence"
        )
        assert evidence["strategies"][0]["successes"] == 1
        snapshot = client.get("/improvement").json()
        assert len(snapshot["active"]) == 1
        assert all(e["outcome"] == "verified_success" for e in snapshot["evaluations"])
        assert snapshot["gaps"][0]["status"] == "resolved_demo_gap"
        assert snapshot["gaps"][0]["occurrences"] == 1


def test_failed_independent_tests_never_activate(tmp_path):
    wrong = 'def run(arguments):\n    return {"median": 999.0}\n'
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport([PLAN, action(), draft(wrong), draft(wrong)]),
        )
    ) as client:
        response = request(client)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "candidate_build_failed"
        assert not client.get("/improvement").json()["active"]
        runs = [
            e["result"]
            for e in response.json()["detail"]["activity"]
            if e["kind"] == "candidate_tests"
        ]
        assert len(runs) == 2 and all(r["state"] == "failed" for r in runs)
        assert all(r["payload"]["execution"]["exit_code"] == 0 for r in runs)


def test_failed_source_revised_using_actual_findings(tmp_path):
    wrong = 'def run(arguments):\n    return {"median": 999.0}\n'
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport(
                [PLAN, action(), draft(wrong), draft(), action(), action(kind="finish")]
            ),
        )
    ) as client:
        response = request(client)
        assert response.status_code == 200, response.text
        runs = [
            e["result"]
            for e in response.json()["activity"]
            if e["kind"] == "candidate_tests"
        ]
        assert [r["state"] for r in runs] == ["failed", "passed"]
        candidate = client.get("/improvement").json()["candidates"][0]
        detail = client.get(f"/improvement/candidates/{candidate['id']}").json()
        assert len(detail["versions"]) == 3
        assert detail["versions"][1]["source"] == wrong





def test_missing_capability_without_task_optin_is_blocker():
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True), transport([PLAN, action()])
        )
    ) as client:
        response = request(client, demo_tools=False)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "demo_required"
        assert not client.get("/improvement").json()["candidates"]


def test_unsupported_gap_never_generates_code():
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport([PLAN, action("send_email", {})]),
        )
    ) as client:
        response = request(client, goal="Send an email")
        assert response.status_code == 200
        kinds = [e["kind"] for e in response.json()["activity"]]
        assert "escalated_to_human" in kinds
        assert not client.get("/improvement").json()["candidates"]


def test_invalid_arguments_recovered_with_meaningful_change():
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [
                    PLAN,
                    action("calculate", {"operation": "add", "a": 17}),
                    action("calculate", {"operation": "add", "a": 17, "b": 25}),
                    action(kind="finish", summary="42"),
                ]
            ),
        )
    ) as client:
        response = request(client, goal="Add 17 and 25", demo_tools=False)
        assert response.status_code == 200, response.text
        kinds = [e["kind"] for e in response.json()["activity"]]
        assert (
            "tool_failed" in kinds
            and "reflection" in kinds
            and "tool_executed" in kinds
        )
        assert (
            client.get("/improvement").json()["evaluations"][0]["outcome"]
            == "verified_success"
        )


def test_identical_failure_stops_loop():
    bad = action("calculate", {"operation": "add", "a": 10})
    with TestClient(
        create_app(Settings(_env_file=None), transport([PLAN, bad, bad]))
    ) as client:
        response = request(client, goal="Add 10 and 2", demo_tools=False)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "repeated_tool_call"


def test_division_by_zero_cannot_be_recovered_by_changing_user_operand():
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [PLAN, action("calculate", {"operation": "divide", "a": 10, "b": 0})]
            ),
        )
    ) as client:
        response = request(client, goal="Divide 10 by 0", demo_tools=False)
        assert (
            response.status_code == 422
            and response.json()["detail"]["code"] == "agent_blocked"
        )


def test_dynamic_tools_chain_real_calculation_into_report():
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [
                    PLAN,
                    action("calculate", {"operation": "multiply", "a": 6, "b": 7}),
                    action(
                        "create_report",
                        {
                            "title": "Calculation",
                            "format": "md",
                            "content": "6 × 7 = 42",
                        },
                    ),
                    action(kind="finish", summary="Created report with result 42."),
                ]
            ),
        )
    ) as client:
        response = request(
            client, goal="Multiply 6 by 7 and save a report.", demo_tools=False
        )
        assert response.status_code == 200, response.text
        executed = [
            e for e in response.json()["activity"] if e["kind"] == "tool_executed"
        ]
        assert [e["tool"] for e in executed] == ["calculate", "create_report"]
        assert executed[0]["result"]["data"]["value"] == 42
        assert executed[1]["result"]["data"]


def test_real_subprocess_timeout_and_error():
    adapter = DemoSubprocess(
        Settings(_env_file=None, demo_mode=True, demo_timeout_seconds=1)
    )
    result = adapter.run(
        "def run(arguments):\n    while True:\n        pass\n", [{"numbers": [1]}]
    )
    assert result["timeout"] and result["exit_code"] != 0 and result["results"] is None
    result = adapter.run("def run(arguments):\n    return 1 / 0\n", [{"numbers": [1]}])
    assert result["exit_code"] == 0 and result["results"] == [
        {"ok": False, "error": "ZeroDivisionError"}
    ]


@pytest.mark.parametrize(
    "source",
    [
        "not python!",
        "import os\ndef run(arguments):\n    return {}\n",
        "def run(arguments):\n    return open('secret').read()\n",
    ],
)
def test_demo_rejects_invalid_source_before_launch(source, monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail("Must not launch invalid source")

    monkeypatch.setattr("subprocess.Popen", prohibited)
    with pytest.raises(SandboxError):
        DemoSubprocess(Settings(_env_file=None, demo_mode=True)).run(source, [])


def test_demo_executor_disabled_has_no_fallback(monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail("Disabled adapter must not launch")

    monkeypatch.setattr("subprocess.Popen", prohibited)
    with pytest.raises(SandboxError):
        DemoSubprocess(Settings(_env_file=None, demo_mode=False)).run(SOURCE, [])


def test_real_contract_and_activation_exact_hashes_and_demo_registry_gate():
    settings = Settings(_env_file=None, demo_mode=True)
    database, repository, version = prepared(settings)
    validator = Validator(repository, DemoSubprocess(settings))
    binding = Binding(**bind(version))
    validator.contract(
        ContractInput(**bind(version), cases=cases(), confirm=True),
        "independent-test-oracle",
    )
    run = validator.validate(binding)
    assert run["state"] == "passed"
    assert (
        sum(c["scope"] == "host_input_boundary" for c in run["payload"]["cases"]) == 10
    )
    assert (
        sum(c["scope"] == "sandbox_exact_output" for c in run["payload"]["cases"]) == 7
    )
    activation = Activation(repository, DemoSubprocess(settings))
    with pytest.raises(Conflict):
        activation.decide(
            DecisionInput(**bind(version), action="activate", confirm=True), "test"
        )
    for action_name in ("approve", "activate"):
        activation.decide(
            DecisionInput(**bind(version), action=action_name, confirm=True), "test"
        )
    assert not generated_specs(settings, database)
    spec = generated_specs(settings, database, allow_demo=True)["candidate_median_test"]
    assert spec.implementation(spec.validate({"numbers": [10, 2, 8]})) == {
        "median": 8.0
    }
    tampered = bind(version) | {"source_hash": "0" * 64}
    with pytest.raises(Conflict):
        activation.decide(
            DecisionInput(**tampered, action="deactivate", confirm=True), "test"
        )
    assert not generated_specs(
        settings.model_copy(update={"demo_mode": False}), database, allow_demo=True
    )


def test_schema_boundaries_independent_cases():
    suite = cases()
    assert {c.category for c in suite} == {
        "valid",
        "boundary",
        "invalid",
        "malformed",
        "security",
    }
    original = copy.deepcopy(suite)
    suggested = [c.model_dump() for c in cases()[:2]]
    suggested[0]["expected"] = {"median": 999}
    assert suite == original and cases() == original


def test_real_version_comparison_activation_and_rollback():
    settings = Settings(_env_file=None, demo_mode=True)
    database, repository, original = prepared(settings)
    # An unseen-input defect is intentionally introduced in this lifecycle fixture.
    weak = SOURCE.replace(
        'values = sorted(arguments["numbers"])',
        'values = sorted(arguments["numbers"])\n    if values == [3, 8]:\n        return {"median": 0.0}',
    )
    base = repository.append(original["candidate_id"], artifact(weak), original["id"])
    adapter = DemoSubprocess(settings)
    validator, activation = Validator(repository, adapter), Activation(
        repository, adapter
    )
    validator.contract(
        ContractInput(**bind(base), cases=cases(), confirm=True),
        "independent-test-oracle",
    )
    assert validator.validate(Binding(**bind(base)))["state"] == "passed"
    for decision in ("approve", "activate"):
        activation.decide(
            DecisionInput(**bind(base), action=decision, confirm=True), "test"
        )
    replacement = repository.append(
        base["candidate_id"], artifact(replaces="candidate_median_test"), base["id"]
    )
    from app.improvement.models import Case

    extension = cases() + [
        Case(
            label="new even regression",
            category="valid",
            input={"numbers": [3, 8]},
            expected={"median": 5.5},
        )
    ]
    validator.contract(
        ContractInput(**bind(replacement), cases=extension, confirm=True),
        "independent-test-oracle",
    )
    run = validator.validate(Binding(**bind(replacement)))
    assert run["state"] == "passed"
    assert run["payload"]["comparison"]["baseline_passed"] == 7
    assert run["payload"]["comparison"]["candidate_passed"] == 8
    for decision in ("approve", "activate"):
        activation.decide(
            DecisionInput(**bind(replacement), action=decision, confirm=True), "test"
        )
    assert activation.active()[0]["id"] == replacement["id"]
    activation.decide(
        DecisionInput(**bind(base), action="rollback", confirm=True), "test"
    )
    assert activation.active()[0]["id"] == base["id"]
    # Previous source and both validation runs remain immutable evidence.
    assert len(repository.detail(base["candidate_id"])["versions"]) == 4


def test_active_demo_tool_reused_after_app_restart():
    settings = Settings(_env_file=None, demo_mode=True)
    with TestClient(
        create_app(
            settings,
            transport([PLAN, action(), draft(), action(), action(kind="finish")]),
        )
    ) as client:
        first = request(client)
        assert first.status_code == 200, first.text
    with TestClient(
        create_app(settings, transport([PLAN, action(), action(kind="finish")]))
    ) as client:
        second = request(client)
        assert second.status_code == 200, second.text
        kinds = [event["kind"] for event in second.json()["activity"]]
        assert "tool_reused" in kinds and "candidate_generation" not in kinds


def test_output_size_limit_cannot_be_claimed_as_success():
    adapter = DemoSubprocess(Settings(_env_file=None, demo_mode=True))
    result = adapter.run(
        'def run(arguments):\n    return {"median": "x" * 100000}\n', [{"numbers": [1]}]
    )
    assert result["output_limit"] and result["results"] is None


def test_unrecovered_tool_failure_cannot_finish_successfully():
    sequence = [
        PLAN,
        action("calculate", {"operation": "add", "a": 1, "b": 2}),
        action("create_report", {"format": "bad", "content": "3"}),
    ] + [action(kind="finish")] * 4
    with TestClient(
        create_app(Settings(_env_file=None), transport(sequence))
    ) as client:
        result = request(client, goal="Add 1 and 2 and save a report", demo_tools=False)
        assert (
            result.status_code == 422
            and result.json()["detail"]["code"] == "iteration_limit"
        )
        assert "final_answer" not in [
            event["kind"] for event in result.json()["detail"]["activity"]
        ]


def test_plain_greeting_finishes_without_a_tool():
    settings = Settings(_env_file=None)
    with TestClient(
        create_app(
            settings,
            transport([PLAN, action(kind="finish", summary="Hello! How can I help?")]),
        )
    ) as client:
        response = request(client, "hi", demo_tools=False)
    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Hello! How can I help?"


def test_numeric_question_still_needs_a_tool():
    settings = Settings(_env_file=None)
    with TestClient(
        create_app(settings, transport([PLAN] + [action(kind="finish", summary="42")] * 6))
    ) as client:
        response = request(client, "What is 17 plus 25?", demo_tools=False)
    assert response.status_code == 422
