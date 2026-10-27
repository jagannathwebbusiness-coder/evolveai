"""Opt-in OS isolation probes, separate from the model API. Never run code on the host."""

import os

import pytest

from app.config import Settings
from app.improvement.sandbox import DockerSandbox

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(
        os.getenv("EVOLVE_RUN_SANDBOX_TEST") != "1",
        reason="Requires explicit opt-in and a reviewed local Docker Desktop Linux runtime/image; no fallback or download",
    ),
]


def adapter():
    sandbox = DockerSandbox(Settings())
    assert sandbox.status()["available"], sandbox.status()
    return sandbox


def test_real_sandbox_secret_and_host_file_isolation(tmp_path, monkeypatch):
    secret = "EVOLVE-PROBE-" + os.urandom(16).hex()
    monkeypatch.setenv("EVOLVE_SANDBOX_PROBE_SECRET", secret)
    private = tmp_path / "probe-private.txt"
    private.write_text(secret)
    source = f"""import os
def run(arguments):
    exposed = os.getenv("EVOLVE_SANDBOX_PROBE_SECRET")
    try:
        host = open({str(private)!r}).read()
    except OSError:
        host = None
    return {{"environment": exposed, "host_file": host}}
"""
    result = adapter().run(source, [{}])
    assert result["exit_code"] == 0 and result["results"] == [
        {"ok": True, "value": {"environment": None, "host_file": None}}
    ]
    assert secret not in result["stdout"] and private.read_text() == secret


def test_real_sandbox_no_network():
    source = """import socket
def run(arguments):
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1)
        return {"connected": True}
    except OSError:
        return {"connected": False}
"""
    result = adapter().run(source, [{}])
    assert result["exit_code"] == 0 and result["results"] == [
        {"ok": True, "value": {"connected": False}}
    ]


def test_real_sandbox_timeout():
    result = adapter().run("def run(arguments):\n    while True:\n        pass\n", [{}])
    assert (
        result["timeout"] or result["exit_code"] != 0
    )  # CPU rlimit can precede wall timeout.
    assert result["results"] is None


def test_real_sandbox_memory_limit():
    result = adapter().run(
        'def run(arguments):\n    return {"data": "x" * (400 * 1024 * 1024)}\n', [{}]
    )
    assert result["exit_code"] != 0 or result["results"][0]["ok"] is False
