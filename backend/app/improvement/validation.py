"""Host checks parse code as data; execution uses the selected Docker or DEMO adapter."""

import ast
import json
import subprocess

from ..storage import Conflict, encode, now
from .models import Artifact, Case, digest, loose_equal, value_check
from .business_contracts import BY_CONTRACT, validate_manifest_input
from .sandbox import SandboxError

FORBIDDEN = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "dir",
    "input",
    "breakpoint",
    "help",
    "exit",
    "quit",
    "type",
    "object",
    "memoryview",
}


def static_checks(artifact):
    findings = []
    try:
        tree = ast.parse(artifact.source)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        entry = [node for node in functions if node.name == "run"]
        if (
            len(entry) != 1
            or len(entry[0].args.args) != 1
            or entry[0].args.vararg
            or entry[0].args.kwarg
            or entry[0].args.kwonlyargs
            or entry[0].args.posonlyargs
        ):
            findings.append("Define exactly one run(arguments) entry point.")
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
                findings.append(
                    "Only imports and ordinary function definitions are allowed at module scope."
                )
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name.split(".")[0] for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module.split(".")[0] if node.module else ""]
                )
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.level
                    or set(names) - set(artifact.manifest.dependencies)
                ):
                    findings.append(
                        "Import is undeclared or outside allowed standard library."
                    )
            if isinstance(node, ast.Name) and (
                node.id in FORBIDDEN or node.id.startswith("_")
            ):
                findings.append(
                    "Forbidden dynamic execution, introspection or I/O name."
                )
            if isinstance(node, ast.Attribute) and (
                node.attr.startswith("_")
                or node.attr
                in {
                    "read",
                    "write",
                    "read_text",
                    "write_text",
                    "environ",
                    "system",
                    "popen",
                    "load",
                    "dump",
                    "load_module",
                }
            ):
                findings.append(
                    "Forbidden filesystem, environment or introspection attribute."
                )
            if isinstance(
                node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)
            ):
                findings.append("Unsupported executable construct.")
            if isinstance(node, ast.FunctionDef) and (
                node.decorator_list
                or node.args.defaults
                or node.args.kw_defaults
                or node.returns
                or any(a.annotation for a in node.args.args)
            ):
                findings.append("Decorators, defaults and annotations are not allowed.")
        if len(artifact.source.splitlines()) > 200 or any(
            len(line) > 120 for line in artifact.source.splitlines()
        ):
            findings.append(
                "Source must fit 200 lines and 120 columns; format the revision before testing."
            )
    except (SyntaxError, RecursionError, MemoryError):
        findings.append("Python syntax cannot be safely parsed.")
    return sorted(set(findings))


class Validator:
    def __init__(self, repository, sandbox):
        self.repository, self.sandbox = repository, sandbox

    def contract(self, body, actor):
        with self.repository.database.connect() as db:
            version = self.repository.bound(body, db)
            if version["status"] not in {
                "generated",
                "sandbox_blocked",
                "validation_failed",
            }:
                raise Conflict(
                    "Acceptance contracts can only be attached to a generated version. Revise to change a contract."
                )
            artifact = Artifact(manifest=version["manifest"], source=version["source"])
            if {c.category for c in body.cases} != {
                "valid",
                "boundary",
                "invalid",
                "malformed",
                "security",
            }:
                raise Conflict(
                    "Reviewed suite must include valid, boundary, invalid, malformed and security cases."
                )
            if len({case.label for case in body.cases}) != len(body.cases):
                raise Conflict("Case labels must be unique.")
            for case in body.cases:
                try:
                    validate_manifest_input(version["manifest"], case.input)
                    accepted = True
                except ValueError:
                    accepted = False
                if case.category in {"valid", "boundary"} and (
                    not accepted or case.reject
                ):
                    raise Conflict(
                        "Valid/boundary cases must have accepted inputs and exact expected outputs."
                    )
                if case.category in {"invalid", "malformed", "security"} and (
                    accepted or not case.reject
                ):
                    raise Conflict(
                        "Invalid/malformed/security cases must test rejected inputs at the host boundary."
                    )
                if not case.reject:
                    try:
                        value_check(artifact.manifest.output_schema, case.expected)
                    except ValueError as exc:
                        raise Conflict(
                            "Expected result does not satisfy the output contract."
                        ) from exc
            cases = [c.model_dump(mode="json") for c in body.cases]
            if db.execute(
                "SELECT 1 FROM contracts WHERE version_id=?", (version["id"],)
            ).fetchone():
                raise Conflict(
                    "Reviewed contracts are immutable. Create a revision for changes."
                )
            db.execute(
                "INSERT INTO contracts VALUES(?,?,?,?,?)",
                (version["id"], encode(cases), digest(cases), actor, now()),
            )
            db.execute(
                "UPDATE candidate_states SET status='generated',error=NULL WHERE version_id=?",
                (version["id"],),
            )
        return self.repository.detail(version["candidate_id"])

    def validate(self, binding):
        with self.repository.database.connect() as db:
            version = self.repository.bound(binding, db)
            if version["status"] not in {
                "generated",
                "validation_failed",
                "sandbox_blocked",
            }:
                raise Conflict(
                    "Only generated, failed or blocked versions can be validated."
                )
            contract = db.execute(
                "SELECT * FROM contracts WHERE version_id=?", (version["id"],)
            ).fetchone()
        self.repository.state(version["id"], "validating")
        artifact = Artifact(manifest=version["manifest"], source=version["source"])
        findings = static_checks(artifact)
        suite_hash = contract["suite_hash"] if contract else digest([])
        payload = {
            "static_findings": findings,
            "cases": [],
            "sandbox": self.sandbox.status(),
            "comparison": None,
            "limitations": [
                "Model-generated tests are suggestions. Exact human-reviewed expected outputs are the oracle.",
                "Input rejection tests run at the trusted host boundary; candidate code never receives invalid inputs.",
                "Static checks do not prove isolation or correctness on unseen inputs.",
            ],
        }
        if artifact.manifest.demo_contract:
            payload["limitations"][
                0
            ] = "Backend-defined independent demo oracle supplies expected outputs before source generation. Model tests are suggestions."
            payload["limitations"].append(
                "DEMO: host subprocess execution is not a secure sandbox; timeout and output limits do not isolate filesystem, network or memory."
            )
        state = "failed" if findings else "blocked"
        if not findings and not contract:
            payload["limitations"].append(
                "Attach a human-reviewed acceptance suite before executable validation."
            )
        elif not findings and contract:
            cases = [Case.model_validate(c) for c in json.loads(contract["cases"])]
            inputs, executable = [], []
            for case in cases:
                if case.reject:
                    try:
                        validate_manifest_input(version["manifest"], case.input)
                        passed = False
                    except ValueError:
                        passed = True
                    payload["cases"].append(
                        {
                            "label": case.label,
                            "category": case.category,
                            "passed": passed,
                            "scope": "host_input_boundary",
                        }
                    )
                else:
                    inputs.append(case.input)
                    executable.append(case)
            try:
                execution = self.sandbox.run(artifact.source, inputs)
                payload["execution"] = execution
                if not isinstance(execution, dict):
                    raise ValueError("Sandbox response must be an object.")
                results = execution.get("results")
                good_execution = (
                    not execution.get("timeout")
                    and not execution.get("output_limit")
                    and execution.get("exit_code") == 0
                    and isinstance(results, list)
                    and len(results) == len(executable)
                )
                for index, case in enumerate(executable):
                    result = results[index] if good_execution else {}
                    passed = isinstance(result, dict) and result.get("ok") is True
                    if passed:
                        try:
                            value_check(
                                artifact.manifest.output_schema, result.get("value")
                            )
                            capability = BY_CONTRACT.get(
                                artifact.manifest.demo_contract
                            )
                            if capability:
                                passed = capability.matches(
                                    result["value"], case.expected
                                )
                            elif (
                                artifact.manifest.test_provenance
                                == "model_generated_independent"
                            ):
                                passed = loose_equal(result["value"], case.expected)
                            else:
                                passed = digest(result["value"]) == digest(
                                    case.expected
                                )
                        except (ValueError, TypeError, KeyError):
                            passed = False
                    payload["cases"].append(
                        {
                            "label": case.label,
                            "category": case.category,
                            "passed": passed,
                            "scope": "sandbox_exact_output",
                            "actual": result,
                        }
                    )
                state = (
                    "passed"
                    if good_execution and all(c["passed"] for c in payload["cases"])
                    else "failed"
                )
                if artifact.manifest.replaces:
                    comparison = self.compare(version, artifact, cases)
                    payload["comparison"] = comparison
                    if not comparison["improved"]:
                        state = "failed"
                else:
                    with self.repository.database.connect() as db:
                        previous = db.execute(
                            "SELECT after_id FROM activation_audit WHERE name=? AND after_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                            (artifact.manifest.name,),
                        ).fetchone()
                    if previous and previous[0] != version["id"]:
                        payload["comparison"] = {
                            "improved": False,
                            "reason": "A previously active version exists. Declare replaces and retain its regression contract even when it is currently deactivated.",
                        }
                        state = "failed"
            except SandboxError as exc:
                payload["limitations"].append(str(exc))
                state = "blocked"
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                TypeError,
                KeyError,
            ):
                payload["limitations"].append(
                    "Sandbox execution or result decoding failed. No successful validation was recorded; inspect the local runtime and retry."
                )
                state = "failed"
        record = self.repository.save_run(version, state, suite_hash, payload)
        self.repository.state(
            version["id"],
            {
                "passed": "awaiting_approval",
                "failed": "validation_failed",
                "blocked": "sandbox_blocked",
            }[state],
        )
        return record

    def compare(self, version, artifact, cases):
        with self.repository.database.connect() as db:
            active = db.execute(
                "SELECT version_id FROM active_tools WHERE name=?",
                (artifact.manifest.name,),
            ).fetchone()
            if not active:
                active = db.execute(
                    "SELECT after_id FROM activation_audit WHERE name=? AND after_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                    (artifact.manifest.name,),
                ).fetchone()
            if not active or active[0] == version["id"]:
                return {
                    "improved": False,
                    "reason": "Replacement requires an active approved baseline.",
                }
            baseline = self.repository.version(active[0], db)
            contract = db.execute(
                "SELECT cases FROM contracts WHERE version_id=?", (baseline["id"],)
            ).fetchone()
        previous = json.loads(contract[0])
        current = [c.model_dump(mode="json") for c in cases]
        if any(case not in current for case in previous):
            return {
                "improved": False,
                "reason": "All baseline regression cases must remain identical.",
            }
        executable = [c for c in cases if not c.reject]
        result = self.sandbox.run(baseline["source"], [c.input for c in executable])
        if not isinstance(result, dict):
            raise ValueError("Baseline sandbox response must be an object.")
        values = result.get("results")
        if (
            result.get("timeout")
            or result.get("output_limit")
            or result.get("exit_code") != 0
            or not isinstance(values, list)
            or len(values) != len(executable)
            or any(
                not isinstance(item, dict)
                or type(item.get("ok")) is not bool
                or (item["ok"] and "value" not in item)
                for item in values
            )
        ):
            return {
                "improved": False,
                "reason": "Baseline execution failed; comparison is inconclusive.",
                "baseline_execution": result,
            }
        passed = sum(
            isinstance(r, dict)
            and r.get("ok") is True
            and digest(r.get("value")) == digest(c.expected)
            for r, c in zip(values, executable)
        )
        return {
            "improved": passed < len(executable),
            "metric": "exact_output_cases_passed",
            "baseline_version_id": baseline["id"],
            "baseline_passed": passed,
            "candidate_passed": len(executable),
            "total": len(executable),
            "baseline_execution": result,
            "reason": "All prior contract cases retained; candidate must pass all and improve at least one extension case.",
        }
