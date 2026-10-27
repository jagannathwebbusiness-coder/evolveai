"""Trusted harness, ONLY launched inside the isolated Linux container.

Never import/run this file as a candidate executor on the Windows host.
Python restrictions here are output hygiene; the Docker VM/container is the boundary.
"""

import contextlib
import importlib.util
import io
import json
import resource


class BoundedOutput(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 8192:
            raise ValueError("Candidate output limit")
        return super().write(value)


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (240 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with open("/fixture/job.json", encoding="utf-8") as handle:
        inputs = json.load(handle)
    results, stdout, stderr = [], BoundedOutput(), BoundedOutput()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        spec = importlib.util.spec_from_file_location(
            "candidate", "/fixture/candidate.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for arguments in inputs:
            try:
                value = module.run(arguments)
                json.dumps(value, allow_nan=False)
                results.append({"ok": True, "value": value})
            except Exception as exc:
                results.append({"ok": False, "error": type(exc).__name__})
    print(
        json.dumps(
            {
                "results": results,
                "candidate_stdout": stdout.getvalue(),
                "candidate_stderr": stderr.getvalue(),
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
