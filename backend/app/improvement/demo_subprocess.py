"""Explicit local educational executor. NO OS isolation, network or memory guarantee."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from time import perf_counter

from .sandbox import SandboxError


def limit_process(process, memory_mb):
    """Windows job object: per-process memory cap, no child processes, kill on close."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    # PROCESS_MEMORY | ACTIVE_PROCESS | KILL_ON_JOB_CLOSE
    info.BasicLimitInformation.LimitFlags = 0x100 | 0x8 | 0x2000
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = memory_mb * 1024 * 1024
    if not kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    ):
        kernel32.CloseHandle(job)
        return None
    if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
        kernel32.CloseHandle(job)
        return None
    return lambda: kernel32.CloseHandle(job)


class DemoSubprocess:
    policy = "local-demo-subprocess-v1"

    def __init__(self, settings):
        self.settings = settings
        self.runner = Path(__file__).with_name("demo_runner.py")
        # Windows venv launchers create a second Python process. Launch the actual
        # interpreter so a timeout kills the process executing the candidate.
        self.python = str(
            Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        )
        self.identity = (
            "host-python:"
            + hashlib.sha256(
                self.python.encode() + sys.version.encode() + self.runner.read_bytes()
            ).hexdigest()
        )

    def status(self):
        return {
            "available": self.settings.demo_mode,
            "enabled": self.settings.demo_mode,
            "policy": self.policy,
            "image": self.identity,
            "timeout_seconds": self.settings.demo_timeout_seconds,
            "secure_sandbox": False,
            "hardening": {
                "restricted_builtins": True,
                "import_allowlist": True,
                "memory_limit_mb": (
                    self.settings.demo_memory_mb if os.name == "nt" else None
                ),
                "child_process_limit": os.name == "nt",
            },
            "reason": "DEMO: host Python subprocess, NOT a secure sandbox. Restricted builtins/imports and (on Windows) a memory/process cap reduce accidents, but there is no filesystem or network isolation. "
            + (
                "Explicit per-task opt-in is required."
                if self.settings.demo_mode
                else "DEMO_MODE is disabled."
            ),
        }

    def run(self, source, inputs):
        if not self.settings.demo_mode:
            raise SandboxError(
                "Local demo execution is disabled; no subprocess was started."
            )
        # Defense in depth only. Caller also validates the manifest and AST.
        from .validation import static_checks
        from .models import DEPENDENCIES, Artifact

        artifact = Artifact(
            source=source,
            manifest={
                "name": "candidate_demo_check",
                "description": "Demo static check",
                "dependencies": sorted(DEPENDENCIES),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "generated_tests": [
                    {"label": "static", "category": "valid", "input": {}},
                    {"label": "static2", "category": "boundary", "input": {}},
                ],
            },
        )
        if static_checks(artifact):
            raise SandboxError(
                "Demo source failed static checks; no subprocess was started."
            )
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="evolve-demo-") as temporary:
            directory = Path(temporary)
            (directory / "candidate.py").write_text(source, encoding="utf-8")
            (directory / "job.json").write_text(
                json.dumps(inputs, allow_nan=False), encoding="utf-8"
            )
            shutil.copyfile(self.runner, directory / "runner.py")
            environment = {
                k: v
                for k, v in os.environ.items()
                if k.upper() in {"SYSTEMROOT", "WINDIR"}
            }
            environment.update(TEMP=temporary, TMP=temporary)
            process = subprocess.Popen(
                [self.python, "-I", "-B", str(directory / "runner.py")],
                cwd=directory,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                release = limit_process(process, self.settings.demo_memory_mb)
            except Exception:  # Hardening is best effort; the timeout still applies.
                release = None
            buffers = {"stdout": bytearray(), "stderr": bytearray()}
            exceeded = threading.Event()

            def stop():
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        if process.poll() is None:
                            raise

            def drain(pipe, key):
                try:
                    while chunk := pipe.read(1024):
                        remaining = 32768 - len(buffers[key])
                        buffers[key].extend(chunk[: max(remaining, 0)])
                        if len(chunk) > remaining:
                            exceeded.set()
                            stop()
                            break
                finally:
                    pipe.close()

            readers = [
                threading.Thread(
                    target=drain, args=(getattr(process, key), key), daemon=True
                )
                for key in buffers
            ]
            for reader in readers:
                reader.start()
            timeout = False
            try:
                process.wait(timeout=self.settings.demo_timeout_seconds)
            except subprocess.TimeoutExpired:
                timeout = True
                stop()
                process.wait(timeout=3)
            finally:
                stop()
                for reader in readers:
                    reader.join(timeout=2)
                if release:
                    release()
            result = {
                key: value.decode("utf-8", errors="replace")
                for key, value in buffers.items()
            }
            result.update(
                exit_code=process.returncode,
                duration_ms=round((perf_counter() - started) * 1000),
                timeout=timeout,
                output_limit=exceeded.is_set(),
                policy=self.policy,
                image=self.identity,
                secure_sandbox=False,
                job_limited=release is not None,
                results=None,
                child_pid=process.pid,
            )
            if not timeout and not exceeded.is_set() and process.returncode == 0:
                try:
                    document = json.loads(result["stdout"])
                    values = document["results"]
                    if (
                        not isinstance(values, list)
                        or len(values) != len(inputs)
                        or any(
                            not isinstance(v, dict) or type(v.get("ok")) is not bool
                            for v in values
                        )
                    ):
                        raise ValueError("Malformed demo results")
                    result["results"] = values
                except (ValueError, KeyError, TypeError, RecursionError):
                    result[
                        "stderr"
                    ] += "\nInvalid child-process results; no success claimed."
            return result
