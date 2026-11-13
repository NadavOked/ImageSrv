"""#2594 (שלב 1): `room_state` הוא חישובי מצב טהורים — קריאה בלבד, בלי FastAPI.

שלושה דברים שהפיצול מבטיח, ושאף טסט קיים אינו בודק:

1. ייבוא `room_state` אינו טוען את FastAPI ואינו טוען את `server.db` (שם יושבים
   `writing`, `update_one`, `journal` ו-`_write_lock`). נבדק בתהליך נפרד, מול
   בקרה חיובית: ייבוא `room` **כן** טוען את שניהם — אחרת הבדיקה אינה יכולה להיכשל.
2. אין במודול SQL כותב ואין `commit` — כך שגם חיבור שהועבר אליו אינו נכתב.
3. כל פונקציה שהועברה עדיין זמינה כ-`room.<שם>`, **ואותו אובייקט** — קוראים קיימים
   (וטסטים קיימים) ממשיכים לעבוד בלי שינוי.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "server" / "rounds" / "room_state.py"

PROBE = ("import sys, importlib; importlib.import_module(sys.argv[1]); "
         "print(' '.join(m for m in ('fastapi', 'starlette', 'server.db') if m in sys.modules))")


def _loaded(module: str) -> set[str]:
    out = subprocess.run([sys.executable, "-c", PROBE, module], cwd=REPO,
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return set(out.stdout.split())


def _tree() -> ast.Module:
    return ast.parse(STATE.read_text(encoding="utf-8"))


def test_importing_room_state_loads_neither_fastapi_nor_the_db_writer():
    assert {"fastapi", "server.db"} <= _loaded("server.rounds.room")   # the probe can see them
    assert _loaded("server.rounds.room_state") == set()


def test_room_state_names_no_fastapi_and_no_db_module():
    imported = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    bad = {m for m in imported
           if m.split(".")[0] in ("fastapi", "starlette") or m == "server.db"}
    assert not bad, bad


def test_room_state_has_no_writing_sql_and_no_commit():
    tree = _tree()
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    writes = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.IGNORECASE)
    sql = [n.value for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
    assert any("SELECT" in s for s in sql)                 # the scan reads the queries
    assert [s for s in sql if writes.search(s)] == []
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "execute" in calls                              # the scan sees the calls
    assert not calls & {"commit", "executemany", "executescript", "rollback"}


def test_room_reexports_every_function_of_room_state_as_the_same_object():
    from server.rounds import room, room_state
    names = [n.name for n in _tree().body if isinstance(n, ast.FunctionDef)]
    assert len(names) >= 20
    assert [n for n in names if getattr(room, n, None) is not getattr(room_state, n)] == []
