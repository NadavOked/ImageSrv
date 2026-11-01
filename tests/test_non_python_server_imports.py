"""ייבוא של מודולי השרת מתוך קבצים שאינם פייתון (#2239).

סקריפטי ההתקנה והיחידות מריצים `python3 -m server.X` ו-`from server.X import Y`
בתוך heredoc. שינוי שם של מודול לא נתפס שם על ידי שום import בטסטים: ב-#2250
`deploy/setup-boot-server.sh` נשאר עם `server.console.console_tls`, והתקנה נקייה
הייתה נופלת בשלב תעודת הקונסולה. הטסט הזה פותר כל הפניה כזו מול העץ.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FROM_RE = re.compile(r"^\s*from (server(?:\.\w+)*) import ([\w ,]+)", re.M)
DASH_M_RE = re.compile(r"-m (server(?:\.\w+)*)")


def tracked_non_python() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "deploy", "installer", "tools", "boot"],
        cwd=REPO, capture_output=True, text=True, check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.split()
    return [REPO / p for p in out if not p.endswith((".py", ".md"))]


def module_path(dotted: str) -> Path | None:
    base = REPO.joinpath(*dotted.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def name_resolves(dotted: str, name: str) -> bool:
    pkg = REPO.joinpath(*dotted.split("."))
    if (pkg / f"{name}.py").is_file() or (pkg / name / "__init__.py").is_file():
        return True
    src = module_path(dotted).read_text(encoding="utf-8")
    return re.search(rf"^(def|class) {name}\b|^{name}\s*[:=]|\bimport .*\b{name}\b", src, re.M) is not None


def references() -> list[tuple[str, str, list[str]]]:
    refs = []
    for path in tracked_non_python():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for m in FROM_RE.finditer(text):
            names = [n.strip() for n in m.group(2).split(",") if n.strip()]
            refs.append((rel, m.group(1), names))
        for m in DASH_M_RE.finditer(text):
            refs.append((rel, m.group(1), []))
    return refs


def test_the_scan_sees_the_installer_imports() -> None:
    """סריקה שלא מצאה כלום אינה ירוקה (עיקרון 5)."""
    files = {rel for rel, _, _ in references()}
    assert "deploy/setup-boot-server.sh" in files
    assert len(references()) > 15


def test_every_server_module_named_outside_python_exists() -> None:
    broken = []
    for rel, dotted, names in references():
        if module_path(dotted) is None:
            broken.append(f"{rel}: {dotted}")
            continue
        broken += [f"{rel}: from {dotted} import {n}" for n in names
                   if not name_resolves(dotted, n)]
    assert not broken, "מודולים שלא קיימים:\n" + "\n".join(broken)
