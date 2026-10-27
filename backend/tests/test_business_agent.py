"""Offline MODEL fixtures, REAL candidate subprocesses and persistent lifecycle tests.

Real local-model acceptance is separately run by demo_business.py.
"""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.improvement.activation import Activation
from app.improvement.business_contracts import (
    BY_NAME,
    goal_requirements,
)
from app.improvement.demo_subprocess import DemoSubprocess
from app.improvement.models import Artifact, Binding, ContractInput, DecisionInput
from app.improvement.persistence import Repository
from app.improvement.runtime import generated_specs
from app.improvement.validation import Validator
from app.storage import Database, encode, now
from app.tools.errors import ToolError
from test_agentic_core import PLAN, action, draft, request, transport, bind

SOURCES = {
    "business_revenue": "return {'revenue': float(arguments['units'] * arguments['unit_price'])}",
    "business_profit": "return {'profit': float(arguments['revenue'] - arguments['total_cost'])}",
    "business_profit_margin": "return {'profit_margin_percent': (arguments['revenue'] - arguments['total_cost']) / arguments['revenue'] * 100.0}",
    "business_roi": "return {'roi_percent': arguments['net_profit'] / arguments['investment'] * 100.0}",
    "business_selling_price": "return {'selling_price': arguments['unit_cost'] / (1 - arguments['target_margin_percent'] / 100.0)}",
    "business_break_even": """p = int(round(arguments['unit_price'] * 100))
v = int(round(arguments['variable_cost'] * 100))
f = int(round(arguments['fixed_cost'] * 100))
c = p - v
u = (f + c - 1) // c
return {'break_even_units': u, 'break_even_revenue': float(u * p / 100), 'contribution_per_unit': float(c / 100)}""",
}


def source(name):
    # Format fixtures for the existing 120-column source check.
    body = SOURCES[name]
    body = (
        body.replace("return {'", "return {\n    '")
        .replace(", '", ",\n    '")
        .replace("}", "\n}")
    )
    return (
        "def run(arguments):\n"
        + "\n".join("    " + line for line in body.splitlines())
        + "\n"
    )


def goal(name, arguments=None):
    capability = BY_NAME[name]
    label = name.removeprefix("business_").replace("_", " ")
    return (
        "Calculate "
        + label
        + " using "
        + json.dumps(arguments or capability.samples[0])
        + "."
    )


def events(response):
    value = response.json()
    return value.get("activity", value.get("detail", {}).get("activity", []))


@pytest.mark.parametrize("name", list(SOURCES))
def test_all_business_contracts_execute_independent_cases_and_register(name):
    settings = Settings(_env_file=None, demo_mode=True)
    database = Database(settings)
    database.initialize()
    repository = Repository(database)
    gap_id = str(uuid4())
    with database.connect() as db:
        db.execute(
            "INSERT INTO gaps VALUES(?,?,?,?)",
            (gap_id, gap_id, encode({"occurrences": 1}), now()),
        )
    candidate_id, proposed_id = repository.proposed(gap_id)
    c = BY_NAME[name]
    version = repository.append(
        candidate_id,
        Artifact(
            source=source(name),
            manifest={
                "name": "candidate_contract_test",
                "description": c.description,
                "demo_contract": c.contract,
                "input_schema": c.inputs,
                "output_schema": c.outputs,
                "generated_tests": [case.model_dump() for case in c.cases()[:2]],
            },
        ),
        proposed_id,
    )
    adapter = DemoSubprocess(settings)
    validator, activation = Validator(repository, adapter), Activation(
        repository, adapter
    )
    validator.contract(
        ContractInput(**bind(version), cases=c.cases(), confirm=True),
        "independent-business-oracle",
    )
    run = validator.validate(Binding(**bind(version)))
    assert run["state"] == "passed", run
    assert run["payload"]["execution"]["child_pid"] > 0
    assert run["payload"]["execution"]["exit_code"] == 0
    assert all(case["passed"] for case in run["payload"]["cases"])
    for decision in ("approve", "activate"):
        activation.decide(
            DecisionInput(**bind(version), action=decision, confirm=True), "test"
        )
    spec = generated_specs(settings, database, True)["candidate_contract_test"]
    assert c.matches(
        spec.implementation(spec.validate(c.samples[1])), c.oracle(c.samples[1])
    )
    # Registered schemas/semantic guards reject invalid inputs before candidate execution.
    for case in c.cases():
        if case.reject:
            with pytest.raises(ToolError):
                spec.validate(case.input)


@pytest.mark.parametrize(
    "name", ["business_break_even", "business_roi", "business_selling_price"]
)
def test_business_generation_activation_execution_restart_reuse(name):
    settings = Settings(_env_file=None, demo_mode=True)
    c = BY_NAME[name]
    with TestClient(
        create_app(
            settings, transport([PLAN, action(name, c.samples[0]), draft(source(name))])
        )
    ) as client:
        first = request(client, goal=goal(name))
        assert first.status_code == 200, first.text
        kinds = [e["kind"] for e in events(first)]
        assert (
            kinds.index("tool_failed")
            < kinds.index("candidate_tests")
            < kinds.index("candidate_activated")
            < kinds.index("tool_executed")
        )
        assert (
            client.get("/improvement").json()["evaluations"][0]["outcome"]
            == "verified_success"
        )
        first_id = client.get("/improvement").json()["active"][0]["version_id"]
    with TestClient(
        create_app(settings, transport([PLAN, action(name, c.samples[1])]))
    ) as client:
        second = request(client, goal=goal(name, c.samples[1]))
        assert second.status_code == 200, second.text
        kinds = [e["kind"] for e in events(second)]
        assert "tool_reused" in kinds and "candidate_generation" not in kinds
        assert client.get("/improvement").json()["active"][0]["version_id"] == first_id
        assert all(
            item["outcome"] == "verified_success"
            for item in client.get("/improvement").json()["evaluations"]
        )
        assert (
            client.get("/improvement").json()["gaps"][0]["status"]
            == "resolved_demo_gap"
        )


@pytest.mark.parametrize(
    "name", ["business_revenue", "business_profit", "business_profit_margin"]
)
def test_existing_calculator_prevents_redundant_generation(name):
    c = BY_NAME[name]
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport([PLAN, action(name, c.samples[0])]),
        )
    ) as client:
        response = request(client, goal=goal(name))
        assert response.status_code == 200, response.text
        assert not client.get("/improvement").json()["candidates"]
        assert [
            e["tool"] for e in events(response) if e["kind"] == "tool_executed"
        ] == ["calculate"]


def test_failed_business_tests_block_activation_and_keep_failures():
    name = "business_break_even"
    wrong = "def run(arguments):\n    return {'break_even_units': 1, 'break_even_revenue': 1.0, 'contribution_per_unit': 1.0}\n"
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport(
                [
                    PLAN,
                    action(name, BY_NAME[name].samples[0]),
                    draft(wrong),
                    draft(wrong),
                ]
            ),
        )
    ) as client:
        response = request(client, goal=goal(name))
        assert (
            response.status_code == 422
            and response.json()["detail"]["code"] == "candidate_build_failed"
        )
        snapshot = client.get("/improvement").json()
        assert snapshot["active"] == []
        runs = [e["result"] for e in events(response) if e["kind"] == "candidate_tests"]
        assert len(runs) == 2 and all(r["state"] == "failed" for r in runs)
        assert all(r["payload"]["execution"]["exit_code"] == 0 for r in runs)
        assert "candidate_activated" not in [e["kind"] for e in events(response)]


def test_irrelevant_tools_cannot_execute_and_do_not_poison_recovery():
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [
                    PLAN,
                    action("analyze_csv", {"upload_id": "sales_data"}),
                    action("list_workspace_files", {"directory": ""}),
                    action("read_workspace_file", {"path": "sales_data.csv"}),
                    action(kind="finish", summary="42"),
                ]
            ),
        )
    ) as client:
        response = request(client, goal="Multiply 6 by 7", demo_tools=False)
        assert response.status_code == 200, response.text
        activity = events(response)
        assert [e["tool"] for e in activity if e["kind"] == "tool_executed"] == [
            "calculate"
        ]
        assert all(
            e["result"]["error"]["code"] == "irrelevant_tool"
            for e in activity
            if e["kind"] == "tool_failed"
        )
        assert next(
            e["result"]["offered"]
            for e in activity
            if e["kind"] == "tool_catalog_filtered"
        ) == ["calculate"]
        assert "replan" in [e["kind"] for e in activity]


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "not JSON",
        '{"operation":"add","a":17}',
        '{"operation":"add","a":17,"b":25}',
        42,
    ],
)
def test_malformed_or_encoded_native_arguments_recover(raw):
    native = action("calculate", {"placeholder": 1})
    native["arguments"] = raw
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport([PLAN, native, action(kind="finish", summary="42")]),
        )
    ) as client:
        response = request(client, goal="Add 17 and 25", demo_tools=False)
        assert response.status_code == 200, response.text
        assert response.json()["message"] == "42"
        activity = events(response)
        executed = [e for e in activity if e["kind"] == "tool_executed"]
        assert len(executed) == 1
        assert ("argument_decoding" in [e["kind"] for e in activity]) == (
            not isinstance(raw, str) or raw == "not JSON"
        )
        for event in activity:
            if event["kind"] == "tool_failed":
                details = event["result"]["error"]["details"]
                assert details["fields"] and details["schema"]["required"] == [
                    "operation",
                    "a",
                    "b",
                ]


def test_hallucinated_file_rejected_then_actual_listing_and_read():
    settings = Settings(_env_file=None)
    settings.evolve_workspace_dir.mkdir(parents=True)
    (settings.evolve_workspace_dir / "notes.md").write_text("actual notes")
    with TestClient(
        create_app(
            settings,
            transport(
                [
                    PLAN,
                    action("read_workspace_file", {"path": "sales_data.csv"}),
                    action("list_workspace_files", {"directory": ""}),
                    action("list_workspace_files", {"directory": "."}),
                    action("read_workspace_file", {"path": "notes.md"}),
                    action(kind="finish", summary="Read actual notes"),
                ]
            ),
        )
    ) as client:
        response = request(
            client, goal="Read notes.md from the workspace", demo_tools=False
        )
        assert response.status_code == 200, response.text
        executed = [e for e in events(response) if e["kind"] == "tool_executed"]
        assert [e["tool"] for e in executed] == [
            "list_workspace_files",
            "read_workspace_file",
        ]
        failed = [e for e in events(response) if e["kind"] == "tool_failed"]
        assert failed[0]["result"]["error"]["code"] == "ungrounded_file"
        assert (
            failed[1]["result"]["error"]["details"]["fields"][0]["path"]
            == "$.directory"
        )


def test_plan_or_unrelated_success_cannot_satisfy_business_task():
    # A correct calculator call for unrelated numbers cannot satisfy break-even.
    sequence = [
        PLAN,
        action("calculate", {"operation": "add", "a": 1, "b": 2}),
        action(kind="finish", summary="Planned successfully"),
    ]
    with TestClient(
        create_app(Settings(_env_file=None), transport(sequence))
    ) as client:
        response = request(client, goal=goal("business_break_even"), demo_tools=False)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "demo_required"
        assert "final_answer" not in [e["kind"] for e in events(response)]


def test_bounded_recovery_and_no_generation_for_irrelevant_unknown_name():
    with TestClient(
        create_app(
            Settings(_env_file=None, agentic_max_recoveries=1),
            transport(
                [
                    PLAN,
                    action("analyze_csv", {}),
                    action("read_workspace_file", {"path": "fake.md"}),
                ]
            ),
        )
    ) as client:
        response = request(client, goal="Add 17 and 25", demo_tools=False)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "recovery_limit"
        assert not client.get("/improvement").json()["candidates"]


@pytest.mark.parametrize(
    "task",
    [
        "Calculate ROI with investment=1000",
        'Calculate ROI using {"net_profit": 30, "investment": 0}',
        'Calculate break-even using {"fixed_cost": 10, "unit_price": 20, "variable_cost": 20}',
    ],
)
def test_missing_or_semantically_invalid_business_inputs_do_not_invent_values(task):
    with TestClient(
        create_app(Settings(_env_file=None, demo_mode=True), transport([]))
    ) as client:
        response = request(client, goal=task)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] in {
            "missing_business_data",
            "invalid_business_input",
        }
        assert not client.get("/improvement").json()["candidates"]


def test_original_plain_language_business_scenario_is_grounded():
    task = "A business sells 200 units monthly at Rs600 each. Variable cost is Rs300 per unit, and monthly fixed costs are Rs45,000. Analyze revenue, total costs, profit, profit margin, break-even units, break-even revenue, and profit if sales increase by 20%."
    requirements = goal_requirements(task)
    assert len(requirements) == 5
    outcomes = {
        item["label"]: item["capability"].oracle(item["arguments"])
        for item in requirements
    }
    assert outcomes["business_revenue"] == {"revenue": 120000.0}
    assert outcomes["business_profit"] == {"profit": 15000.0}
    assert outcomes["business_profit_margin"] == {"profit_margin_percent": 12.5}
    assert outcomes["business_break_even"]["break_even_units"] == 150
    assert outcomes["projected_profit"] == {"profit": 27000.0}


def test_schema_aware_grounding_accepts_equivalent_json_numbers_not_integer_coercion():
    c = BY_NAME["business_revenue"]
    assert c.arguments_match(
        {"units": 200, "unit_price": 600}, {"units": 200, "unit_price": 600.0}
    )
    assert not c.arguments_match(
        {"units": 200.0, "unit_price": 600}, {"units": 200, "unit_price": 600}
    )
    assert not c.arguments_match(
        {"units": True, "unit_price": 600}, {"units": 1, "unit_price": 600}
    )
    task = 'Calculate revenue using {"units":200,"unit_price":600.0}.'
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport([PLAN, action(c.name, {"units": 200, "unit_price": 600})]),
        )
    ) as client:
        response = request(client, goal=task, demo_tools=False)
        assert response.status_code == 200, response.text
        assert "tool_failed" not in [e["kind"] for e in events(response)]


def test_currency_boundary_exposes_naive_float_ceiling_defect():
    c = BY_NAME["business_break_even"]
    case = c.samples[-1]
    assert c.oracle(case)["break_even_units"] == 10
    naive = "def run(arguments):\n    c = arguments['unit_price'] - arguments['variable_cost']\n    u = int(arguments['fixed_cost'] / c)\n    if u * c < arguments['fixed_cost']:\n        u += 1\n    return {'break_even_units': u}\n"
    actual = DemoSubprocess(Settings(_env_file=None, demo_mode=True)).run(naive, [case])
    assert (
        actual["exit_code"] == 0
        and actual["results"][0]["value"]["break_even_units"] == 11
    )


def test_business_failed_candidate_is_revised_before_activation():
    name = "business_roi"
    c = BY_NAME[name]
    wrong = "def run(arguments):\n    return {'roi_percent': 999.0}\n"
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport(
                [PLAN, action(name, c.samples[0]), draft(wrong), draft(source(name))]
            ),
        )
    ) as client:
        response = request(client, goal=goal(name))
        assert response.status_code == 200, response.text
        assert [
            e["result"]["state"]
            for e in events(response)
            if e["kind"] == "candidate_tests"
        ] == ["failed", "passed"]
        assert len(client.get("/improvement").json()["active"]) == 1


@pytest.mark.parametrize(
    "task,name",
    [
        (
            "Use calculate to find the profit margin with revenue 12500 and cost 8750.",
            "business_profit_margin",
        ),
        (
            "Calculate break-even revenue with fixed costs 45000, selling price 600 and variable cost 300.",
            "business_break_even",
        ),
        ("Calculate ROI with net profit 300 and investment 1000.", "business_roi"),
    ],
)
def test_named_inputs_are_not_mistaken_for_additional_requested_outputs(task, name):
    requirements = goal_requirements(task)
    assert [item["capability"].name for item in requirements] == [name]
    from app.agent.policy import TaskPolicy
    from app.schemas import ChatRequest

    body = ChatRequest(
        messages=[{"role": "user", "content": task}], use_tools=True, agentic=True
    )
    assert not TaskPolicy(task, body).missing_operands


def test_long_model_source_formatted_without_logic_change_and_original_preserved():
    import ast
    from app.improvement.source_formatting import format_candidate_source

    long_source = """def run(arguments):
  p = int(round(arguments['unit_price'] * 100))
  v = int(round(arguments['variable_cost'] * 100))
  f = int(round(arguments['fixed_cost'] * 100))
  c = p - v
  units = (f + c - 1) // c
  return {'break_even_units': units, 'break_even_revenue': float(units * p / 100), 'contribution_per_unit': float(c / 100)}
"""
    formatted = format_candidate_source(long_source)
    assert max(map(len, formatted.splitlines())) <= 120
    assert ast.dump(ast.parse(formatted)) == ast.dump(ast.parse(long_source))
    assert format_candidate_source("not python!") == "not python!"
    c = BY_NAME["business_break_even"]
    with TestClient(
        create_app(
            Settings(_env_file=None, demo_mode=True),
            transport([PLAN, action(c.name, c.samples[0]), draft(long_source)]),
        )
    ) as client:
        response = request(client, goal=goal(c.name))
        assert response.status_code == 200, response.text
        assert "candidate_formatted" in [e["kind"] for e in events(response)]
        candidate = client.get("/improvement").json()["candidates"][0]
        detail = client.get(f"/improvement/candidates/{candidate['id']}").json()
        assert detail["versions"][0]["source"] == formatted
        assert detail["versions"][1]["source"] == long_source
        assert detail["versions"][0]["status"] == "active"


def test_original_scenario_requires_all_outputs_and_generates_only_missing_tool():
    task = "A business sells 200 units monthly at Rs600 each. Variable cost is Rs300 per unit, and monthly fixed costs are Rs45,000. Analyze revenue, total costs, profit, profit margin, break-even units, break-even revenue, and profit if sales increase by 20%. Recommend three actions."
    sequence = [
        PLAN,
        action("business_revenue", {"units": 200, "unit_price": 600}),
        action("business_profit", {"revenue": 120000, "total_cost": 105000}),
        action("business_profit_margin", {"revenue": 120000, "total_cost": 105000}),
        action(
            "business_break_even",
            {"fixed_cost": 45000, "unit_price": 600, "variable_cost": 300},
        ),
        draft(source("business_break_even")),
        action(kind="finish", summary="Premature: outline is finished"),
        action(
            kind="finish",
            summary="Recommendations: reduce waste; review fixed costs; test demand.",
        ),
    ]
    with TestClient(
        create_app(Settings(_env_file=None, demo_mode=True), transport(sequence))
    ) as client:
        response = request(client, goal=task)
        assert response.status_code == 200, response.text
        activity = events(response)
        assert len([e for e in activity if e["kind"] == "result_verified"]) == 5
        assert len([e for e in activity if e["kind"] == "candidate_generation"]) == 1
        assert len([e for e in activity if e["kind"] == "tool_executed"]) == 5
        assert "projected_profit: 27000" in response.json()["message"]
        assert "total_cost: 105000" in response.json()["message"]
        assert "not independently verified" in response.json()["message"]
        assert (
            client.get("/improvement").json()["evaluations"][0]["outcome"]
            == "verified_success"
        )


def test_final_business_answer_uses_execution_and_full_qualitative_advice_only():
    task = 'Calculate profit margin using {"revenue":100,"total_cost":70} and recommend three actions.'
    untrusted = (
        "Using candidate_fabricated, I executed a tool and verified margin 900%.\n"
        + "Unsupported narrative. " * 100
        + "\n1. Reduce waste.\n2. Review fixed costs.\n3. Test customer demand."
    )
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [
                    PLAN,
                    action(
                        "business_profit_margin", {"revenue": 100, "total_cost": 70}
                    ),
                    action(kind="finish", summary=untrusted),
                ]
            ),
        )
    ) as client:
        response = request(client, goal=task, demo_tools=False)
        assert response.status_code == 200, response.text
        message = response.json()["message"]
        assert "profit_margin_percent: 30" in message
        assert "900" not in message and "candidate_fabricated" not in message
        assert "Unsupported narrative" not in message
        assert "3. Test customer demand." in message
        assert not response.json()["truncated"]
        assert "final_text_filtered" in [e["kind"] for e in events(response)]


def test_missing_or_numeric_recommendations_recover_before_completion():
    task = 'Calculate profit margin using {"revenue":100,"total_cost":70} and recommend two actions.'
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [
                    PLAN,
                    action(
                        "business_profit_margin", {"revenue": 100, "total_cost": 70}
                    ),
                    action(
                        kind="finish",
                        summary="1. Increase margin to 99%.\n2. Executed a new tool.",
                    ),
                    action(
                        kind="finish",
                        summary="1. Review supplier costs.\n2. Test customer demand.",
                    ),
                ]
            ),
        )
    ) as client:
        response = request(client, goal=task, demo_tools=False)
        assert response.status_code == 200, response.text
        assert "99%" not in response.json()["message"]
        activity = events(response)
        assert any(
            e["kind"] == "reflection"
            and e.get("result", {}).get("category") == "final_response_validation"
            for e in activity
        )
        assert len([e for e in activity if e["kind"] == "tool_executed"]) == 1


def test_business_report_response_links_actual_saved_report():
    task = 'Calculate profit using {"revenue":100,"total_cost":70} and save a report.'
    with TestClient(
        create_app(
            Settings(_env_file=None),
            transport(
                [
                    PLAN,
                    action("business_profit", {"revenue": 100, "total_cost": 70}),
                    action(
                        "create_report",
                        {"title": "Profit", "format": "md", "content": "Profit: 30"},
                    ),
                    action(
                        kind="finish", summary="Saved a report at a fabricated path."
                    ),
                ]
            ),
        )
    ) as client:
        response = request(client, goal=task, demo_tools=False)
        assert response.status_code == 200, response.text
        saved = next(
            e["result"]["data"]
            for e in events(response)
            if e["kind"] == "tool_executed" and e["tool"] == "create_report"
        )
        assert saved["download_url"] in response.json()["message"]
        assert "fabricated" not in response.json()["message"]
        # Vite strips /api when proxying the website's download link to FastAPI.
        download = client.get(saved["download_url"].removeprefix("/api"))
        assert download.status_code == 200 and download.text == "Profit: 30"


def test_csv_column_words_are_not_business_operands():
    from types import SimpleNamespace

    from app.agent.policy import TaskPolicy

    body = SimpleNamespace(upload_ids=[uuid4()])
    policy = TaskPolicy("Analyze the attached CSV; report missing values and mean revenue.", body)
    assert policy.csv and policy.missing_operands == []


def test_create_report_tool_name_counts_as_an_explicit_report_request():
    from types import SimpleNamespace

    from app.agent.policy import TaskPolicy

    policy = TaskPolicy(
        "Use create_report with title 'Weekly summary', format 'md', content 'x'.",
        SimpleNamespace(upload_ids=[]),
    )
    assert policy.report


def test_numberless_business_request_asks_for_inputs_instead_of_failing():
    with TestClient(
        create_app(Settings(_env_file=None), transport([]))
    ) as client:
        response = request(
            client,
            goal="create a tool that helps me manage my whole profit and loss",
            demo_tools=False,
        )
        assert response.status_code == 200, response.text
        assert "need your numbers" in response.json()["message"]
        kinds = [e["kind"] for e in response.json()["activity"]]
        assert "needs_input" in kinds
