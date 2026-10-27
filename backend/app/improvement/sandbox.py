"""Opt-in Docker Desktop Linux isolation. Never invokes candidate Python on the host."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from time import perf_counter
from uuid import uuid4


class SandboxError(Exception):
    pass


class DockerSandbox:
    policy = "docker-desktop-linux-v1"
    cleanup_failed = False

    def __init__(self, settings):
        self.settings = settings
        self.executable = shutil.which("docker")
        self.environment = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "HOME",
                "LOCALAPPDATA",
                "APPDATA",
            }
        }

    def cli(self, *args):
        if not self.executable:
            raise SandboxError("Docker CLI is not installed.")
        return [self.executable, "--context", "desktop-linux", *args]

    def inspect(self, *args):
        result = subprocess.run(
            self.cli(*args),
            capture_output=True,
            timeout=5,
            env=self.environment,
            shell=False,
        )
        if result.returncode or len(result.stdout) > 64000:
            raise SandboxError(
                "Local Docker Desktop inspection failed; execution is blocked."
            )
        return json.loads(result.stdout)

    def status(self):
        ready, reason = (
            False,
            "Candidate execution is disabled. Review-only mode; no candidate code runs.",
        )
        if self.cleanup_failed:
            reason = "Container cleanup failed. Restart only after inspecting/removing the named evolve-candidate container."
        elif self.settings.sandbox_enabled:
            try:
                if not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", self.settings.sandbox_image
                ):
                    raise SandboxError(
                        "Set SANDBOX_IMAGE to an existing trusted local sha256 image ID, not a tag."
                    )
                context = self.inspect("context", "inspect", "desktop-linux")[0]
                endpoint = context["Endpoints"]["docker"]["Host"]
                if not endpoint.startswith("npipe:////./pipe/"):
                    raise SandboxError(
                        "Only the local Docker Desktop named-pipe endpoint is supported."
                    )
                info = self.inspect("info", "--format", "{{json .}}")
                if info.get("OSType") != "linux" or not any(
                    "seccomp" in str(v) for v in info.get("SecurityOptions", [])
                ):
                    raise SandboxError(
                        "Linux containers with the default seccomp policy are required."
                    )
                image = self.inspect("image", "inspect", self.settings.sandbox_image)[0]
                if image.get("Os") != "linux" or image.get("Config", {}).get("Volumes"):
                    raise SandboxError(
                        "Trusted image must be Linux and declare no volumes."
                    )
                if image.get("Id") != self.settings.sandbox_image:
                    raise SandboxError(
                        "Image identity differs from configured immutable ID."
                    )
                allowed_env = {
                    "PATH",
                    "LANG",
                    "GPG_KEY",
                    "PYTHON_VERSION",
                    "PYTHON_SHA256",
                    "PYTHON_PIP_VERSION",
                    "PYTHON_SETUPTOOLS_VERSION",
                    "PYTHON_GET_PIP_URL",
                    "PYTHON_GET_PIP_SHA256",
                }
                if any(
                    entry.split("=", 1)[0] not in allowed_env
                    for entry in image.get("Config", {}).get("Env", [])
                ):
                    raise SandboxError(
                        "Image declares nonstandard environment values. Use a reviewed secret-free runtime image."
                    )
                ready, reason = (
                    True,
                    "Opt-in Docker Desktop Linux adapter ready. Operator must trust the reviewed image and local daemon.",
                )
            except (
                SandboxError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
            ) as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, SandboxError)
                    else "Sandbox prerequisites could not be verified. Execution is blocked."
                )
        return {
            "available": ready,
            "enabled": self.settings.sandbox_enabled,
            "policy": self.policy,
            "image": self.settings.sandbox_image or None,
            "reason": reason,
            "timeout_seconds": self.settings.sandbox_timeout_seconds,
        }

    def command(self, directory, name):
        # A single fresh staging directory, never the repository, data or workspace roots.
        if "," in str(directory):
            raise SandboxError("Sandbox staging path cannot contain a comma.")
        return self.cli(
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--pids-limit=32",
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=1",
            "--ulimit=nofile=64:64",
            "--ulimit=fsize=1048576:1048576",
            "--user=65534:65534",
            "--ipc=none",
            "--log-driver=none",
            "--no-healthcheck",
            "--workdir=/output",
            "--tmpfs=/output:rw,noexec,nosuid,nodev,size=8m,mode=1777",
            "--mount",
            f"type=bind,source={directory},target=/fixture,readonly",
            "--entrypoint=python",
            self.settings.sandbox_image,
            "-I",
            "-B",
            "/fixture/runner.py",
        )

    def run(self, source, inputs):
        status = self.status()
        if not status["available"]:
            raise SandboxError(status["reason"])
        name = "evolve-candidate-" + uuid4().hex
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="evolve-sandbox-") as temporary:
            directory = Path(temporary).resolve()
            (directory / "candidate.py").write_text(source, encoding="utf-8")
            (directory / "job.json").write_text(
                json.dumps(inputs, allow_nan=False), encoding="utf-8"
            )
            shutil.copyfile(
                Path(__file__).with_name("sandbox_runner.py"), directory / "runner.py"
            )
            process = subprocess.Popen(
                self.command(directory, name),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.environment,
                shell=False,
            )
            streams = {"stdout": bytearray(), "stderr": bytearray()}
            exceeded = threading.Event()

            def drain(pipe, key):
                while chunk := pipe.read(1024):
                    remaining = 32768 - len(streams[key])
                    streams[key].extend(chunk[: max(remaining, 0)])
                    if len(chunk) > remaining:
                        exceeded.set()
                        process.kill()
                        break
                pipe.close()

            readers = [
                threading.Thread(
                    target=drain, args=(getattr(process, key), key), daemon=True
                )
                for key in streams
            ]
            for reader in readers:
                reader.start()
            timeout = False
            try:
                process.wait(timeout=self.settings.sandbox_timeout_seconds)
            except subprocess.TimeoutExpired:
                timeout = True
                process.kill()
                process.wait(timeout=3)
            finally:
                # Kill the actual container too; killing the attached CLI alone is insufficient.
                try:
                    cleanup = subprocess.run(
                        self.cli("rm", "--force", name),
                        capture_output=True,
                        timeout=5,
                        env=self.environment,
                        shell=False,
                    )
                    if cleanup.returncode and b"No such container" not in (
                        cleanup.stderr or b""
                    ):
                        raise SandboxError("Container removal could not be confirmed.")
                except (OSError, subprocess.SubprocessError, SandboxError):
                    type(self).cleanup_failed = True
                    raise SandboxError(
                        "Container cleanup failed. Disable sandbox and inspect the named evolve-candidate container before continuing."
                    )
                for reader in readers:
                    reader.join(timeout=2)
            result = {
                "stdout": streams["stdout"].decode("utf-8", errors="replace"),
                "stderr": streams["stderr"].decode("utf-8", errors="replace"),
                "exit_code": process.returncode,
                "duration_ms": round((perf_counter() - started) * 1000),
                "timeout": timeout,
                "output_limit": exceeded.is_set(),
                "policy": self.policy,
                "image": self.settings.sandbox_image,
            }
            result["results"] = None
            if not timeout and not exceeded.is_set() and process.returncode == 0:
                try:
                    document = json.loads(result["stdout"])
                    if (
                        not isinstance(document, dict)
                        or not isinstance(document.get("results"), list)
                        or len(document["results"]) != len(inputs)
                        or any(
                            not isinstance(item, dict)
                            or type(item.get("ok")) is not bool
                            or (item["ok"] and "value" not in item)
                            for item in document["results"]
                        )
                    ):
                        raise ValueError("Invalid runner response")
                    result["results"] = document["results"]
                except ValueError:
                    result["stderr"] += "\nRunner did not return valid bounded results."
            return result
