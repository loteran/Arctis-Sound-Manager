# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A function-local import must not shadow a name the function already used.

Python decides a name is local for the *whole* function body if anything in it
binds that name — including an ``import`` half way down. So this:

    from PySide6.QtCore import QTimer      # module level

    def __init__(self):
        QTimer.singleShot(...)            # line 145  -> UnboundLocalError
        ...
        from PySide6.QtCore import QTimer  # line 159 -> makes QTimer local

raises ``UnboundLocalError: cannot access local variable 'QTimer'`` even though
the module-level import is right there. It shipped exactly once, took down the
main window on open, and reads as a missing import when it is the opposite.

Only the dangerous shape is flagged: a local import whose name is *used earlier*
in the same function. Importing inside a function is a normal and deliberate
pattern here (deferring optional or expensive dependencies), so a local import
that is not preceded by a use of its name is left alone.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _local_import_bindings(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:
    """{name: earliest line it is bound by a local import} within *fn*.

    Nested functions are skipped: they have their own scope, so an import
    inside one does not make the name local to the enclosing function.
    """
    bindings: dict[str, int] = {}
    nested = {n for node in ast.walk(fn)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
              and node is not fn
              for n in ast.walk(node)}

    for node in ast.walk(fn):
        if node in nested or not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for alias in node.names:
            name = (alias.asname or alias.name).split(".")[0]
            bindings[name] = min(bindings.get(name, node.lineno), node.lineno)
    return bindings


def _uses_before(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                 name: str, line: int) -> int | None:
    """The first line inside *fn* that reads *name* before *line*."""
    for node in ast.walk(fn):
        if (isinstance(node, ast.Name) and node.id == name
                and isinstance(node.ctx, ast.Load) and node.lineno < line):
            return node.lineno
    return None


def _offences() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                       # pragma: no cover
            continue
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for name, import_line in _local_import_bindings(fn).items():
                used = _uses_before(fn, name, import_line)
                if used is not None:
                    found.append(
                        f"{path.relative_to(SRC.parent)}:{used} — {fn.name}() uses "
                        f"'{name}' before the local import on line {import_line}, "
                        f"making it local for the whole function (UnboundLocalError)")
    return found


def test_no_local_import_shadows_an_earlier_use():
    offences = _offences()
    assert not offences, "\n".join(offences)


def test_detector_catches_the_shape_it_is_meant_to(tmp_path):
    """Guard the guard: a checker that never fires protects nothing."""
    module = tmp_path / "bad.py"
    module.write_text(
        "from PySide6.QtCore import QTimer\n"
        "\n"
        "def build():\n"
        "    QTimer.singleShot(0, None)\n"
        "    from PySide6.QtCore import QTimer\n"
        "    return QTimer\n"
    )
    tree = ast.parse(module.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))

    bindings = _local_import_bindings(fn)
    assert "QTimer" in bindings
    assert _uses_before(fn, "QTimer", bindings["QTimer"]) == 4
