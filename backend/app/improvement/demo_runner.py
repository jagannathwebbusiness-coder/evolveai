"""Trusted host CHILD-PROCESS harness for explicitly enabled educational mode.

NOT a secure sandbox. This harness never runs inside FastAPI. Restricted builtins, an
import allowlist, a memory/process cap and static checks reduce accidents and obvious abuse,
but cannot make hostile code safe on the user's account.
"""

import builtins
import contextlib
import importlib.util
import io
import json
from pathlib import Path

ALLOWED_IMPORTS = {
    "math",
    "statistics",
    "decimal",
    "json",
    "re",
    "collections",
    "datetime",
}
SAFE_NAMES = (
    "abs all any bool bytes chr dict divmod enumerate filter float frozenset int "
    "isinstance issubclass len list map max min ord pow print range repr reversed round "
    "set slice sorted str sum tuple zip "
    "ArithmeticError AssertionError Exception IndexError KeyError LookupError "
    "OverflowError RecursionError StopIteration TypeError ValueError ZeroDivisionError "
    "ImportError"
).split()


class BoundedOutput(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 8192:
            raise ValueError("Demo output limit")
        return super().write(value)


def restricted_builtins():
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split(".")[0] not in ALLOWED_IMPORTS:
            raise ImportError("Import is outside the allowed standard-library subset.")
        return real_import(name, globals, locals, fromlist, level)

    safe = {name: getattr(builtins, name) for name in SAFE_NAMES}
    safe["__import__"] = guarded_import
    return safe


def main():
    inputs = json.loads(Path("job.json").read_text(encoding="utf-8"))
    captured, errors, results = BoundedOutput(), BoundedOutput(), []
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(errors):
        spec = importlib.util.spec_from_file_location("candidate", "candidate.py")
        module = importlib.util.module_from_spec(spec)
        module.__dict__["__builtins__"] = restricted_builtins()
        spec.loader.exec_module(module)
        for arguments in inputs:
            try:
                result = module.run(arguments)
                json.dumps(result, allow_nan=False)
                results.append({"ok": True, "value": result})
            except Exception as exc:
                results.append({"ok": False, "error": type(exc).__name__})
    print(
        json.dumps(
            {
                "results": results,
                "candidate_stdout": captured.getvalue(),
                "candidate_stderr": errors.getvalue(),
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
