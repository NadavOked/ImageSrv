"""Operator-visible text never promises v1.1 / v1.2: there is no v1.1, classrooms are v2 (Nadav 31/10, 11/11)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STALE = re.compile(r"במהדורה זו \(v1\.[12]\)|יגיע ב-v1\.[12]")


def test_no_operator_string_names_v1_1_or_v1_2():
    hits = []
    for top in ("server", "agent", "native-gui/src", "installer", "boot"):
        for path in (ROOT / top).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".c", ".h", ".js", ".html"}:
                for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if STALE.search(line):
                        hits.append(f"{path.relative_to(ROOT)}:{n}")
    assert not hits, hits
