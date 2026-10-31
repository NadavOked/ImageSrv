"""‏#1313 — משני שהותקן מה-ISO לא האזין ב-8443.

נמדד במעבדה (12/10): חיפה, ‏role=secondary, ‏imagesrv-server active — ומ-FW-TA
‏`curl https://<חיפה>:8443/` → connection refused. השורש: ‏`server/main.py`
מרים את מאזין ה-enrollment הבין-שרתי רק כשניתן ‏`--interserver-host`, והמתקין
(‏`setup-boot-server.sh`, שגם האשף וגם firstboot מה-ISO קוראים לו) לא העביר
אותו.

שתי חוליות, כל אחת בטסט משלה:

* **המתקין** — קטע בניית ‏`STORAGE_ARGS` מורץ **בפועל** ב-bash (לא חיפוש
  טקסט), והפלט נפתח ב-``build_parser`` האמיתי: משני מקבל
  ‏`--interserver-host <כרטיס השרתים>`, ‏standalone לא.
* **השרת** — ``main`` עם ה-argv הזה רושם את מאזין ``interserver`` על הכרטיס.
  ומשני **שכבר הותקן** (override.conf בלי הדגל — ‏`server-upgrade.sh` אינו
  כותב אותו מחדש) מקבל את המאזין על ``--console-host`` אחרי העדכון.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import server.app
import server.main
from native import requires_native
from test_installer_1131 import BASH

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
PRIMARY = "https://10.30.0.5:8443"

needs_bash = requires_native(("bash", BASH), why="קטע המתקין הוא bash")


# --- המתקין ------------------------------------------------------------------------

def storage_args_block() -> str:
    """הקטע שבונה את ‏STORAGE_ARGS — מה-`if` של התפקיד ועד כתיבת ה-override.

    הקוד עצמו, לא עותק: סטייה בין המתקין לבדיקה היא בדיוק החור."""
    lines = INSTALLER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines)
                 if line == 'if [[ "$STORAGE_ROLE" == "secondary" ]]; then'
                 and lines[i + 1].lstrip().startswith('STORAGE_ARGS="--storage-role secondary'))
    end = next(i for i, line in enumerate(lines) if i > start and line.startswith(
        "write_file /etc/systemd/system/imagesrv-server.service.d/override.conf"))
    return "\n".join(lines[start:end])


def installer_storage_args(role: str, *, servers_if: str = "ens18",
                           console_host: str | None = None) -> list[str]:
    """‏STORAGE_ARGS כפי שהמתקין היה כותב ל-override.conf, מפוצל כמו ב-systemd."""
    prelude = (
        "set -euo pipefail\n"
        f"STORAGE_ROLE={role}\n"
        f"PRIMARY_URL={PRIMARY}\n"
        "HTTP_ROOT=/srv/imagesrv/boot\n"
        "TFTP_ROOT=/srv/tftp\n"
        "IFACE=\n"
        f"SERVERS_IF={servers_if}\n"
        f"CONSOLE_HOST={console_host or servers_if}\n"
    )
    proc = subprocess.run(
        [BASH, "-c", prelude + storage_args_block() + '\nprintf "%s" "$STORAGE_ARGS"\n'],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ}, timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert proc.returncode == 0, proc.stderr
    return shlex.split(proc.stdout)


def parsed(argv: list[str]):
    return server.main.build_parser().parse_args(
        ["--server-url", "http://10.44.12.10:8080", *argv])


@needs_bash
def test_installer_gives_a_secondary_the_interserver_listener_on_the_servers_nic():
    """הבאג כפי שנמדד: override של משני בלי ‏--interserver-host = אין 8443."""
    args = parsed(installer_storage_args("secondary", servers_if="ens18"))
    assert args.storage_role == "secondary"
    assert args.interserver_host == "ens18"


@needs_bash
def test_the_interserver_nic_is_the_servers_nic_even_with_an_explicit_console_host():
    """חומת האש פותחת 8443 על ‏`iifname $SERVERS_IF` (nftables-rules.sh) —
    המאזין חייב לשבת על אותו כרטיס, גם כשהקונסולה קיבלה כתובת מפורשת."""
    args = parsed(installer_storage_args("secondary", servers_if="ens19",
                                         console_host="10.30.0.8"))
    assert args.interserver_host == "ens19"
    assert args.console_host == "10.30.0.8"


@needs_bash
def test_installer_does_not_give_a_standalone_the_interserver_listener():
    """הראשי יוזם; ל-standalone אין מה להאזין ב-8443."""
    argv = installer_storage_args("standalone")
    assert "--interserver-host" not in argv
    assert parsed(argv).interserver_host is None


# --- השרת ------------------------------------------------------------------------

#: ‏#1310: המאזינים מסוג uvicorn שנרשמו בשם ``interserver`` בריצה האחרונה של
#: ``run_main`` — "אתה שם?" של הראשי. ‏run_main עצמו מחזיר רק את ה-threaded
#: (ה-mTLS של המשני), כמו לפני #1310.
ALIVE: list[dict] = []


def run_main(tmp_path: Path, monkeypatch, *, stored_role: str,
             argv: list[str]) -> list[dict]:
    """‏main עם כל החוליות מסביב מנוטרלות — מחזיר את המאזינים ה-threaded
    שנרשמו (‏`interserver` הוא היחיד). התפקיד השמור מוזרק, כמו ה-DB.
    מאזין ה-"אתה שם?" של הראשי (#1310) נרשם ב-``ALIVE``."""
    import asyncio

    import uvicorn

    from server.net import ifaddr, ports
    from server.interserver import primary_alive, storage_nodes

    threaded: list[dict] = []
    ALIVE.clear()
    real_add = ports.Listeners.add

    def fake_add_threaded(self, port_id, **kwargs):
        threaded.append({"id": port_id, **kwargs})

    def recording_add(self, port_id, factories, **kwargs):
        if port_id == "interserver":
            ALIVE.append({"id": port_id, **kwargs})
        return real_add(self, port_id, factories, **kwargs)

    class NoWatcher:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return self

        def stop(self):
            pass

    monkeypatch.setattr(server.app, "create_runtime",
                        lambda *a, **k: SimpleNamespace(conn=None, ctx=None))
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **k: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda listeners: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda name, *a, **k: "10.30.0.8")
    monkeypatch.setattr(ifaddr, "AddressWatcher", NoWatcher)
    monkeypatch.setattr(ports.Listeners, "add_threaded", fake_add_threaded)
    monkeypatch.setattr(ports.Listeners, "add", recording_add)
    monkeypatch.setattr(server.app, "create_alive_app", lambda rt, fp: "alive")
    monkeypatch.setattr(primary_alive, "server_config", lambda *a, **k: object())
    monkeypatch.setattr(storage_nodes, "role", lambda conn: stored_role)
    monkeypatch.setattr(storage_nodes, "ensure_identity", lambda *a, **k: {
        "cert_pem": "", "key_pem": "", "node_id": "sn_x", "server_spki": "0" * 64})
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        *argv])
    server.main.main()
    return threaded


@needs_bash
def test_a_secondary_installed_by_the_installer_registers_the_8443_listener(
        tmp_path: Path, monkeypatch):
    """שרשרת שלמה: ה-argv שהמתקין כותב → ‏main רושם `interserver` על
    הכתובת של כרטיס השרתים, בפורט 8443."""
    argv = installer_storage_args("secondary", servers_if="ens18")
    threaded = run_main(tmp_path, monkeypatch, stored_role="secondary", argv=argv)
    assert [t["id"] for t in threaded] == ["interserver"]
    assert threaded[0]["port"] == 8443
    assert threaded[0]["hosts"] == ["10.30.0.8"]


@needs_bash
def test_a_standalone_installed_by_the_installer_has_no_8443_listener(
        tmp_path: Path, monkeypatch):
    """אין מאזין mTLS. ‏8443 של standalone/ראשי הוא "אתה שם?" בלבד (#1310)."""
    argv = installer_storage_args("standalone", servers_if="ens18")
    assert run_main(tmp_path, monkeypatch, stored_role="standalone", argv=argv) == []
    assert [a["port"] for a in ALIVE] == [8443]


def legacy_secondary_argv() -> list[str]:
    """ה-override.conf של משני שהותקן לפני התיקון (חיפה, v0.71.0) — כפי
    שהמתקין כתב אותו: ‏`--console-host <כרטיס השרתים>`, בלי ‏`--interserver-host`."""
    return ["--storage-role", "secondary", "--primary-url", PRIMARY,
            "--boot-dir", "/srv/imagesrv/boot", "--console-host", "ens18"]


def test_an_already_installed_secondary_gets_the_listener_after_the_update(
        tmp_path: Path, monkeypatch):
    """‏`server-upgrade.sh` אינו כותב את override.conf מחדש, והוא רץ מהעץ
    **הישן** (ה-checkout מחליף את הקובץ תחתיו) — לכן תיקון במתקין או בסקריפט
    השדרוג לא היה מגיע לחיפה. השרת החדש עצמו מרים את 8443 על ‏--console-host."""
    threaded = run_main(tmp_path, monkeypatch, stored_role="secondary",
                        argv=legacy_secondary_argv())
    assert [t["id"] for t in threaded] == ["interserver"]
    assert threaded[0]["hosts"] == ["10.30.0.8"]


def test_the_console_host_fallback_never_opens_8443_on_a_standalone(
        tmp_path: Path, monkeypatch):
    """אותו override בדיוק, אבל standalone — אין מאזין (הראשי אינו מאזין)."""
    argv = ["--storage-role", "standalone", "--boot-dir", "/srv/imagesrv/boot",
            "--console-host", "ens18"]
    assert run_main(tmp_path, monkeypatch, stored_role="standalone", argv=argv) == []


def test_the_fallback_needs_the_secondary_flag_not_only_a_stored_role(
        tmp_path: Path, monkeypatch):
    """תפקיד שמור פגום נקרא fail-closed כ-secondary (#746). זה אינו ראיה
    שהשרת משני — מאזין רשת חדש נפתח רק כשה-unit עצמו אומר ‏`--storage-role
    secondary`."""
    argv = ["--console-host", "ens18"]
    assert run_main(tmp_path, monkeypatch, stored_role="secondary", argv=argv) == []


def test_the_fallback_does_not_bind_8443_on_loopback(tmp_path: Path, monkeypatch):
    """קונסולה על loopback (ברירת המחדל, #770) — הראשי לעולם לא יגיע לשם,
    ו-8443 על 127.0.0.1 רק היה תופס פורט."""
    argv = ["--storage-role", "secondary", "--primary-url", PRIMARY,
            "--console-host", "127.0.0.1"]
    assert run_main(tmp_path, monkeypatch, stored_role="secondary", argv=argv) == []


@pytest.mark.parametrize("explicit", ["10.30.0.9", "ens19"])
def test_an_explicit_interserver_host_still_wins(tmp_path: Path, monkeypatch, explicit):
    argv = legacy_secondary_argv() + ["--interserver-host", explicit]
    threaded = run_main(tmp_path, monkeypatch, stored_role="secondary", argv=argv)
    assert [t["id"] for t in threaded] == ["interserver"]
    expected = explicit if explicit[0].isdigit() else "10.30.0.8"
    assert threaded[0]["hosts"] == [expected]


# --- #1310 × #1313: שני מאזינים על 8443, לעולם לא באותו שרת -------------------

@pytest.mark.parametrize("stored_role", ["standalone", "primary", "secondary"])
@pytest.mark.parametrize("argv", [
    ["--console-host", "ens18"],
    ["--storage-role", "secondary", "--primary-url", PRIMARY, "--console-host", "ens18"],
    ["--storage-role", "secondary", "--primary-url", PRIMARY, "--console-host", "ens18",
     "--interserver-host", "ens18"],
    ["--storage-role", "standalone", "--console-host", "10.30.0.8"],
], ids=["plain", "legacy-secondary", "installer-secondary", "standalone-ip"])
def test_8443_has_at_most_one_listener_per_server(tmp_path: Path, monkeypatch, stored_role, argv):
    """‏"אתה שם?" של הראשי (#1310) עולה כשהתפקיד השמור **אינו** secondary;
    ה-mTLS של המשני (#1313) רק כשהוא secondary. שניהם נרשמים כ-`interserver`
    על 8443 — ושניהם יחד היו bind כפול. בכל צירוף: לכל היותר אחד."""
    threaded = run_main(tmp_path, monkeypatch, stored_role=stored_role, argv=argv)
    assert len(threaded) + len(ALIVE) <= 1, (threaded, ALIVE)
    if stored_role == "secondary":
        assert ALIVE == [], "משני אינו עונה 'אתה שם?'"
    else:
        assert threaded == [], "ראשי/standalone אינו פותח את ה-mTLS"
        assert [a["port"] for a in ALIVE] == [8443], "ראשי עם קונסולה על כרטיס עונה על 8443"
