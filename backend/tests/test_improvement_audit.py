"""Audit regressions: disposable storage and NONEXECUTING malformed-result fixtures."""

import pytest

from app.config import Settings
from app.improvement.activation import Activation
from app.improvement.models import Binding
from app.improvement.runtime import GeneratedSpec
from app.improvement.persistence import Repository
from app.storage import Database
from app.tools.errors import ToolError
from test_improvement import (
    BASELINE_SOURCE,
    CASES,
    FakeSandbox,
    SOURCE,
    artifact,
    bind,
    decision,
    generated,
    reviewed,
    seed,
)


@pytest.fixture
def repository():
    database = Database(Settings(_env_file=None))
    database.initialize()
    seed(database)
    return Repository(database)


@pytest.mark.parametrize("item", [None, 3, [], "malformed"])
def test_audit_malformed_runner_item_is_persisted_failure(repository, item):
    class MalformedAdapter(FakeSandbox):
        def run(self, source, inputs):
            value = super().run(source, inputs)
            value["results"] = [item] * len(inputs)
            return value

    version = generated(repository)
    result = reviewed(repository, MalformedAdapter(), version)
    assert result["state"] == "failed"
    assert repository.version(version["id"])["status"] == "validation_failed"
    assert repository.detail(version["candidate_id"])["versions"][0]["runs"]


def test_audit_production_registry_refuses_unapproved_version(repository):
    from app.improvement.runtime import generated_specs

    version = generated(repository)
    assert generated_specs(repository.database.settings, repository.database) == {}
    sandbox = FakeSandbox()
    spec = GeneratedSpec(version, Activation(repository, sandbox), sandbox)
    with pytest.raises(ToolError, match="unavailable"):
        spec.implementation(spec.validate({"x": 2}))
    assert sandbox.calls == []


def test_audit_malformed_runtime_item_is_tool_error(repository):
    version, sandbox = generated(repository), FakeSandbox()
    activation = Activation(repository, sandbox)
    reviewed(repository, sandbox, version)
    decision(activation, version, "approve")
    decision(activation, version, "activate")
    # Adapter fixture changes its output, not its identity. NO source executes.
    sandbox.run = lambda source, inputs: {"exit_code": 0, "results": [None]}
    spec = GeneratedSpec(version, activation, sandbox)
    with pytest.raises(ToolError, match="unavailable"):
        spec.implementation(spec.validate({"x": 2}))


def test_audit_changed_suite_hash_cannot_reuse_pass(repository):
    from app.storage import Conflict

    version, sandbox = generated(repository), FakeSandbox()
    reviewed(repository, sandbox, version)
    # DB owner can remove triggers; emulate corruption in DISPOSABLE storage only.
    with repository.database.connect() as db:
        db.execute("DROP TRIGGER immutable_contracts_update")
        db.execute("UPDATE contracts SET suite_hash=?", ("0" * 64,))
    with pytest.raises(Conflict, match="contract"):
        decision(Activation(repository, sandbox), version, "approve")
    assert Binding(**bind(version)).source_hash == version["source_hash"]


def test_audit_malformed_baseline_is_inconclusive_not_improvement(repository):
    class MalformedBaseline(FakeSandbox):
        broken = False

        def run(self, source, inputs):
            response = super().run(source, inputs)
            if self.broken and source == BASELINE_SOURCE:
                response["results"] = [None] * len(inputs)
            return response

    sandbox = MalformedBaseline()
    first = generated(repository, artifact(BASELINE_SOURCE))
    activation = Activation(repository, sandbox)
    reviewed(repository, sandbox, first)
    decision(activation, first, "approve")
    decision(activation, first, "activate")
    second = repository.append(
        first["candidate_id"],
        artifact(SOURCE, replaces="candidate_identity"),
        first["id"],
    )
    sandbox.broken = True
    suite = CASES + [
        {
            "label": "upper limit",
            "category": "boundary",
            "input": {"x": 10},
            "expected": {"value": 10},
        }
    ]
    run = reviewed(repository, sandbox, second, suite)
    assert run["state"] == "failed"
    assert run["payload"]["comparison"]["improved"] is False
    assert activation.active()[0]["id"] == first["id"]
