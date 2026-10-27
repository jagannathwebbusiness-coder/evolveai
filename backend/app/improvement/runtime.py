"""Registry adapter: exact tested versions use Docker or explicitly opted-in DEMO subprocesses."""

from dataclasses import dataclass

from pydantic import BaseModel

from ..storage import Conflict
from ..tools.errors import ToolError
from .activation import Activation
from .models import value_check
from .persistence import Repository
from .sandbox import DockerSandbox, SandboxError


GENERAL = "model_generated_independent"


class JSONArguments(BaseModel):
    payload: dict

    def model_dump(self, **kwargs):
        return self.payload


@dataclass(frozen=True)
class GeneratedSpec:
    artifact: dict
    activation: Activation
    sandbox: DockerSandbox
    origin: str = "active_approved"

    @property
    def name(self):
        return self.artifact["manifest"]["name"]

    @property
    def description(self):
        return self.artifact["manifest"]["description"]

    @property
    def version(self):
        return str(self.artifact["version"])

    def definition(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.artifact["manifest"]["input_schema"],
            },
        }

    def validate(self, arguments):
        try:
            from .business_contracts import validate_manifest_input

            validate_manifest_input(self.artifact["manifest"], arguments)
        except ValueError as exc:
            raise ToolError(
                "invalid_tool_arguments",
                "Generated tool input contract failed: " + str(exc)[:500],
            ) from exc
        return JSONArguments(payload=arguments)

    def implementation(self, arguments):
        try:
            version = self.activation.executable(self.artifact["id"])
            execution = self.sandbox.run(version["source"], [arguments.payload])
            if not isinstance(execution, dict):
                raise SandboxError("Sandbox response must be an object.")
            result = execution.get("results")
            if (
                execution.get("timeout")
                or execution.get("output_limit")
                or execution.get("exit_code") != 0
                or not isinstance(result, list)
                or len(result) != 1
                or not isinstance(result[0], dict)
                or result[0].get("ok") is not True
            ):
                raise SandboxError(
                    "Generated tool failed in its configured execution adapter; no successful result was produced."
                )
            value = result[0].get("value")
            value_check(version["manifest"]["output_schema"], value)
            return value
        except (SandboxError, Conflict, ValueError, KeyError, TypeError) as exc:
            raise ToolError(
                "candidate_execution_failed",
                "Approved generated tool is unavailable or failed its execution/output checks. Inspect Self-Development; built-in tools remain available.",
            ) from exc


def generated_specs(settings, database, allow_demo=False):
    from .demo_subprocess import DemoSubprocess
    from .business_contracts import check_manifest

    sandbox = DockerSandbox(settings)
    repository = Repository(database)
    result = {}
    for version in Activation(repository, sandbox).active():
        contract = version["manifest"].get("demo_contract") is not None
        general = version["manifest"].get("test_provenance") == GENERAL
        demo = contract or general
        if demo:
            if not (settings.demo_mode and allow_demo):
                continue
            try:
                if contract:
                    check_manifest(version["manifest"])
            except ValueError:
                continue
        adapter = DemoSubprocess(settings) if demo else sandbox
        result[version["manifest"]["name"]] = GeneratedSpec(
            version,
            Activation(repository, adapter),
            adapter,
            origin=(
                "demo_tested"
                if contract
                else "general_approved" if general else "active_approved"
            ),
        )
    return result
