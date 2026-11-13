"""#1417 — `journal` בתוך `server.main.main()` קיים לפני השימוש הראשון בו.

`from .db import journal` באמצע `main()` הופך את השם למקומי בכל הפונקציה,
ושימוש בו לפני השורה הזאת זורק UnboundLocalError — בעלייה הראשונה אחרי
שהתעודה הוחלפה (#1380), כלומר בדיוק בשדרוג מהכפתור, שאז נחזר.
"""

from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "server" / "main.py"


def _main_fn() -> ast.FunctionDef:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")


def test_every_local_name_imported_inside_main_is_bound_before_its_first_use():
    fn = _main_fn()
    bound: dict[str, int] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                bound[name] = min(bound.get(name, node.lineno), node.lineno)
    late = [(n.id, n.lineno, bound[n.id]) for n in ast.walk(fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id in bound and n.lineno < bound[n.id]]
    assert not late, f"נקרא לפני הייבוא המקומי (UnboundLocalError): {late}"


def test_runtime_is_not_used_before_create_runtime_assigns_it():
    """#1417 (פתיחה מחדש): התיקון הראשון קשר את `journal`, ואז נפל על
    `runtime` — שורת היומן רצה לפני `create_runtime`."""
    fn = _main_fn()
    assigned = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name)
                   and n.id == "runtime" and isinstance(n.ctx, ast.Store))
    early = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name)
             and n.id == "runtime" and isinstance(n.ctx, ast.Load) and n.lineno < assigned]
    assert not early, f"runtime נקרא לפני שנוצר (שורה {assigned}): {early}"
