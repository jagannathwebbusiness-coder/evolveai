"""Whitespace-only formatting, verified by identical ASTs; never repairs logic.

No formatter dependency, code execution or source fallback. The caller preserves
the original model source as a separate immutable version.
"""

import ast
import io
import tokenize


def format_candidate_source(source):
    try:
        original = ast.parse(source)
        output = []
        for line in source.splitlines(keepends=True):
            if len(line.rstrip("\r\n")) <= 120:
                output.append(line)
                continue
            content = line.lstrip().rstrip("\r\n")
            indent = line[: len(line) - len(line.lstrip())]
            edits, depth = {}, 0
            for token in tokenize.generate_tokens(io.StringIO(content).readline):
                if token.type != tokenize.OP:
                    continue
                if token.string in ("(", "[", "{"):
                    depth += 1
                    if token.string == "{":
                        edits[token.end[1]] = "\n" + indent + "    "
                elif token.string in (")", "]", "}"):
                    if token.string == "}" and depth:
                        edits[token.start[1]] = "\n" + indent
                    depth -= 1
                elif token.string == "," and depth:
                    edits[token.end[1]] = "\n" + indent + "    "
            for position, replacement in sorted(edits.items(), reverse=True):
                content = content[:position] + replacement + content[position:]
            output.append(indent + content + ("\n" if line.endswith("\n") else ""))
        formatted = "".join(output)
        if ast.dump(original, include_attributes=False) == ast.dump(
            ast.parse(formatted), include_attributes=False
        ):
            return formatted
    except (
        SyntaxError,
        tokenize.TokenError,
        IndentationError,
        RecursionError,
        ValueError,
    ):
        pass
    return source
