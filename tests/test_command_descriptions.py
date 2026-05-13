from __future__ import annotations

import ast
from pathlib import Path


def _is_filter_command(decorator: ast.AST) -> bool:
    call = decorator if isinstance(decorator, ast.Call) else None
    if call is None:
        return False
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "command"
        and isinstance(func.value, ast.Name)
        and func.value.id == "filter"
    )


def test_every_astrbot_command_handler_has_description_docstring():
    source_path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if any(_is_filter_command(decorator) for decorator in node.decorator_list):
            if not (ast.get_docstring(node) or "").strip():
                missing.append(node.name)

    assert missing == []
