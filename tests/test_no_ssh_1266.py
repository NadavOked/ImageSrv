"""‏#1266 — SSH יצא מהמוצר, והתקנה ותיקה עדיין עולה.

שרתים מותקנים נושאים את מה שמתגי ה-SSH (‏#83) ומפתחות ה-host (‏#1080)
השאירו ב-DB: ‏`ssh:stations`, ‏`ssh:iface:<nic>` וטבלת `machine_ssh_hostkey`.
אין להם משמעות בלי הקוד — העלייה מוחקת אותם בשקט, ואינה נופלת עליהם.
"""

from __future__ import annotations

import re as _re
import sqlite3
from pathlib import Path

import pytest

from server import db


def _old_install(path: Path) -> None:
    """DB של גרסה לפני #1266: ההגדרות והטבלה כפי שהשרת הישן כתב אותן."""
    db.connect(path).close()
    raw = sqlite3.connect(path)
    raw.executescript("""
        INSERT OR REPLACE INTO settings (key, value) VALUES
            ('ssh:stations', '{"enabled": true}'),
            ('ssh:iface:ens18', '{"enabled": true}'),
            ('ssh:iface:ens19', '{"enabled": false}'),
            ('sshish', 'not ours');
        CREATE TABLE IF NOT EXISTS machine_ssh_hostkey (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT NOT NULL,
            seen_at TEXT NOT NULL, hostkey_json TEXT NOT NULL);
        INSERT INTO machine_ssh_hostkey (mac, seen_at, hostkey_json)
            VALUES ('b4:2e:99:07:1a:c4', '2026-09-01T00:00:00+00:00', '{}');
    """)
    raw.commit()
    raw.close()


def test_an_old_install_starts_and_its_ssh_leftovers_are_gone(tmp_path):
    path = tmp_path / "imagectl.db"
    _old_install(path)

    conn = db.connect(path)                      # העלייה עצמה לא נופלת
    keys = {r["key"] for r in conn.execute("SELECT key FROM settings")}
    assert not {k for k in keys if k.startswith("ssh:")}, keys
    assert "sshish" in keys                      # רק המפתחות שלנו נמחקו
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "machine_ssh_hostkey" not in tables
    conn.close()

    db.connect(path).close()                     # ושוב — אידמפוטנטי


# --- שומר: SSH לא חוזר למוצר (#1266) ------------------------------------------
# נדב 11/10: קוד שבוטל נמחק, ולא מוסתר — "שלא יהיה כמו אתמול עם הפורט
# שגילינו שקיים". השומר סורק את קוד המוצר כולו ונכשל על כל אזכור של
# dropbear/sshd או של פורט 22, גם בהערה: הערה שמתארת שירות SSH היא
# הצעד הראשון בחזרתו. גישת הטכנאי של המעבדה חיה ב-overlay שמחוץ לריפו
# (‏IMAGECTL_EXTRA_OVERLAY ב-build_initramfs.sh) — המנגנון עצמו גנרי ואינו
# נוקב ב-SSH, ולכן אין לו פטור כאן. ‏tools/lab (כלי מעבדה) ו-docs (תיעוד
# והיסטוריה) אינם מוצר ואינם נסרקים.


REPO = Path(__file__).resolve().parents[1]

#: מה שנסרק. תיקייה = כל קובץ קוד בתוכה; קובץ = הוא עצמו.
PRODUCT = ("agent", "server", "install", "tools/build_initramfs.sh",
           "tools/iso/build-iso.sh", "tools/iso/packages.txt")
#: סיומות שאינן קוד ואינן נארזות כהתנהגות: תיעוד ותמונות.
NOT_CODE = {".md", ".svg", ".png", ".ico", ".jpg", ".gif", ".woff", ".woff2"}

SSH_TERMS = _re.compile(
    r"dropbear"                                  # שרת ה-SSH שהסוכן ארז (#44)
    r"|(?<![a-z_])sshd\b"                       # שרת ה-SSH של השרת (#83)
    r"|(?<![a-z_])openssh"
    r"|(?<![a-z_])authorized_keys"
    r"|imagectl_deploy"                         # מפתח הטכנאי
    r"|\bssh_(?:switch|hostkey|start|stations|server|nic)\b"
    r"|\bconsole_ssh\b|\bsshd_json\b|--ssh-(?:key|if)\b|--debug-ssh\b"
    r"|/api/console/ssh\b|[\"']/ssh[\"'/]",
    _re.IGNORECASE)
PORT_22 = _re.compile(
    r"\bdport\s+(?:\{[^}]*?)?\b22\b"             # nft: tcp dport 22 / { 22, ... }
    r"|(?<![\d.])22/(?:tcp|udp)\b"               # 22/tcp
    r"|\bSSH_PORT\b"
    r"|\bport\s*[=:]\s*[\"']?22\b(?![\d.])"     # port=22 / "port": "22"
    r"|(?<![\w-])-p\s+[\"']?22\b(?![\d.])",       # dropbear -p 22
    _re.IGNORECASE)


def _product_files() -> list[Path]:
    files: list[Path] = []
    for rel in PRODUCT:
        path = REPO / rel
        assert path.exists(), f"{rel} לא נמצא — השומר היה סורק פחות ממה שהוא מצהיר"
        found = [path] if path.is_file() else [
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() not in NOT_CODE
            and "__pycache__" not in p.parts]
        assert found, f"{rel}: אין קבצים — הבדיקה לא בדקה דבר"
        files += found
    return files


def _hits(pattern: _re.Pattern, keep=lambda rel, line: True) -> list[str]:
    out = []
    for path in _product_files():
        rel = path.relative_to(REPO).as_posix()
        # קובץ שלא נקרא כ-UTF-8 נופל כאן בגלוי — לא מדולג (עיקרון 5).
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and keep(rel, line):
                out.append(f"{rel}:{n}: {line.strip()}")
    return out


#: ה-migration שמוחק את מה שהתקנה ותיקה השאירה (‏db._initialize) — המקום
#: היחיד במוצר שנוקב בשמות הישנים, כדי למחוק אותם.
_CLEANUP = ("server/db.py", ("ssh:stations", "ssh:iface:", "machine_ssh_hostkey", "#1266"))


#: ‏#1979: שחזור לינוקס מוחק את מפתחות ה-SSH **של מערכת ההפעלה המשוחזרת** ומתקין בה
#: יחידה חד-פעמית של `ssh-keygen -A` — כדי שמשוכפלים לא יחלקו מפתחות. זה אינו שרת SSH
#: של ImageSrv ואינו מתג; המילים מותרות רק בפונקציה הזו (`_linux_id_files`, grow.sh).
_TARGET_KEYS = ("agent/lib/grow.sh", ("ssh_host_", "ssh-keygen", "sshd refuses", "ssh.service"))


def _not_the_cleanup(rel: str, line: str) -> bool:
    return not ((rel == _CLEANUP[0] and any(k in line for k in _CLEANUP[1]))
                or (rel == _TARGET_KEYS[0] and any(k in line for k in _TARGET_KEYS[1])))


def test_no_ssh_server_or_its_switches_anywhere_in_the_product():
    hits = _hits(SSH_TERMS, _not_the_cleanup)
    assert not hits, "‏SSH חזר לקוד המוצר (#1266):\n" + "\n".join(hits)


def test_no_port_22_anywhere_in_the_product():
    hits = _hits(PORT_22)
    assert not hits, "‏פורט 22 חזר לקוד המוצר (#1266):\n" + "\n".join(hits)


def test_the_server_exposes_no_ssh_route(tmp_path):
    """לא רק הקונסולה: אין endpoint של SSH בשרת עצמו — גם לא אחד שאין
    כפתור שקורא לו (curl אינו רואה UI). שתי ראיות: הסכמה של האפליקציה,
    ובקשה בלי הזדהות — נתיב קיים עונה 401, נתיב שנמחק עונה 404."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server.app import create_app

    images = tmp_path / "images"
    images.mkdir()
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080")
    paths = list(app.openapi()["paths"])
    # ראיה חיובית שהסכמה כוללת את ראוטרי הקונסולה ולא רק את הסוכן.
    assert "/api/console/ports" in paths and "/api/v1/agent/hello" in paths, paths
    bad = [p for p in paths if _re.search(r"(?<![a-z])ssh", p, _re.IGNORECASE)]
    assert not bad, bad

    anon = TestClient(app)
    assert anon.get("/api/console/ports").status_code == 401     # הבקרה: קיים ומוגן
    for method, url in (("GET", "/api/console/ssh"), ("PUT", "/api/console/ssh/stations"),
                        ("PUT", "/api/console/ssh/interfaces/ens18")):
        assert anon.request(method, url, json={}).status_code == 404, (method, url)
