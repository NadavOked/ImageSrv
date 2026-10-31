"""--http-root של המתקין חייב להגיע לשרת כ---boot-dir (#395).

הדגל יצר תיקייה והעתיק אליה את הקרנל, אבל imagesrv-server.service
הריץ את server.main בלי --boot-dir — ברירת המחדל הקשיחה
/srv/imagesrv/boot. התקנה שנראית מוצלחת, ואף תחנה אינה יכולה לעלות.

ההכרעה: א' — המתקין מזריק --boot-dir דרך IMAGESRV_STORAGE_ARGS ב-drop-in
הקיים. הטסט בונה את argv ש-systemd היה מעביר, ופותח אותו ב-build_parser
האמיתי: בלי התיקון boot_dir נשאר /srv/imagesrv/boot גם כש-HTTP_ROOT אחר.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.main import build_parser

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
UNIT = REPO / "deploy" / "imagesrv-server.service"

DEFAULT_ROOT = "/srv/imagesrv/boot"
CUSTOM_ROOT = "/mnt/bigdisk/imagesrv-boot"

_STORAGE_ASSIGN = re.compile(r'^\s*STORAGE_ARGS="([^"]*)"', re.MULTILINE)


def storage_args_templates() -> dict[str, str]:
    """השמות STORAGE_ARGS כפי שהמתקין כותב אותן, לפי תפקיד."""
    found = _STORAGE_ASSIGN.findall(INSTALLER.read_text(encoding="utf-8"))
    by_role: dict[str, str] = {}
    for template in found:
        if "--storage-role secondary" in template:
            by_role["secondary"] = template
        elif "--storage-role standalone" in template:
            by_role["standalone"] = template
    assert "standalone" in by_role and "secondary" in by_role, found
    return by_role


def exec_start_line(unit_text: str) -> str:
    """ExecStart= אחרי איחוד שורות המשך. לא ExecStartPre."""
    chunks: list[str] = []
    capturing = False
    for line in unit_text.splitlines():
        if line.startswith("ExecStart="):
            capturing = True
            chunk = line[len("ExecStart="):]
            while chunk.endswith("\\"):
                chunk = chunk[:-1].rstrip()
            chunks.append(chunk)
            if not line.rstrip().endswith("\\"):
                break
            continue
        if capturing:
            stripped = line.strip()
            continued = stripped.endswith("\\")
            if continued:
                stripped = stripped[:-1].rstrip()
            chunks.append(stripped)
            if not continued:
                break
    assert chunks, "אין ExecStart ביחידה"
    return " ".join(chunks)


def parsed_boot_dir(http_root: str, role: str = "standalone") -> str:
    """boot_dir ש-server.main היה מקבל אחרי שהמתקין כתב את ה-drop-in."""
    template = storage_args_templates()[role]
    storage = (
        template
        .replace("$HTTP_ROOT", http_root)
        .replace("$PRIMARY_URL", "http://10.0.0.2:8080")
    )
    exec_line = exec_start_line(UNIT.read_text(encoding="utf-8"))
    assert "$IMAGESRV_STORAGE_ARGS" in exec_line, exec_line
    expanded = (
        exec_line
        .replace("${IMAGESRV_URL}", "http://10.44.12.10:8080")
        .replace("$IMAGESRV_STORAGE_ARGS", storage)
    )
    parts = expanded.split()
    idx = parts.index("server.main")
    args = build_parser().parse_args(parts[idx + 1:])
    return args.boot_dir


@pytest.mark.parametrize("role", ["standalone", "secondary"])
def test_a_custom_http_root_reaches_server_main_as_boot_dir(role: str):
    """זה הבאג: HTTP_ROOT שאינו ברירת המחדל, והשרת עדיין מגיש מ-default."""
    assert parsed_boot_dir(CUSTOM_ROOT, role) == CUSTOM_ROOT


def test_the_default_http_root_still_serves_from_the_hardcoded_path():
    """הצד החיובי — ברירת המחדל לא נשברה. עובר גם בלי התיקון, כי שני
    הצדדים כבר היו קשיחים על /srv/imagesrv/boot."""
    assert parsed_boot_dir(DEFAULT_ROOT) == DEFAULT_ROOT
    parser = build_parser()
    args = parser.parse_args(["--server-url", "http://10.44.12.10:8080"])
    assert args.boot_dir == DEFAULT_ROOT


def test_the_http_root_flag_still_exists():
    """הכרעה א' (#395): הדגל נשאר. הסרה שלו הייתה תשובה ב', והיא לא נבחרה."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"--http-root\)", text)
    assert 'HTTP_ROOT="/srv/imagesrv/boot"' in text
