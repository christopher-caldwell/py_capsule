"""Target-project bootstrap. This file runs inside the selected uv environment."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any


def _indent_source(source: str) -> str:
    lines = source.splitlines(keepends=True)
    if not lines or not source.strip():
        return "    pass\n"
    return "".join("    " + line for line in lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("py_capsule worker expected a result file path", file=sys.stderr)
        return 2
    result_path = Path(sys.argv[1])
    try:
        request = json.load(sys.stdin)
        project_dir = Path(request["project_dir"])
        tool_path = Path(request["tool_path"])
        source = tool_path.read_text(encoding="utf-8")
        inputs: dict[str, Any] = request["inputs"]
        injected_globals: dict[str, Any] = request["globals"]

        # Python puts the script directory at sys.path[0]. Restore the selected
        # project as the first import root so project-owned helpers resolve.
        sys.path.insert(0, str(project_dir))

        class Session:
            @staticmethod
            def log_event(message: Any) -> None:
                print(str(message), file=sys.stderr)

        namespace: dict[str, Any] = {
            "__builtins__": __builtins__,
            "__file__": str(tool_path),
            "__name__": "__py_capsule_tool__",
        }
        namespace.update(injected_globals)
        namespace.setdefault("Session", Session)
        parameters = ", ".join(inputs)
        wrapped = (
            f"def __py_capsule_execute__({parameters}):\n"
            + _indent_source(source)
            + "\n"
        )
        code = compile(wrapped, str(tool_path), "exec")
        exec(code, namespace, namespace)
        value = namespace["__py_capsule_execute__"](**inputs)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        result_path.write_text(encoded, encoding="utf-8")
        return 0
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
