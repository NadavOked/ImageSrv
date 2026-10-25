"""מסך בריאות המערכת — רמזור לכל בדיקה, בקונסולה ולא בטרמינל.

העיקרון (נדב): בטרמינל נוגעים פעם אחת, בהתקנה. כדי לדעת אם פורט 67
תפוס ועל ידי מי לא מריצים `ss -ulnp` ביד — פותחים את הקונסולה.

כל בדיקה מחזירה: ok (ירוק) / warn (צהוב) / bad (אדום) / off (אפור —
לא רלוונטי או שאי אפשר לבדוק כאן). הרצת הפקודות מוזרקת, כך שהבדיקות
של הקוד עצמו רצות בלי systemd ובלי רשת.
"""

from __future__ import annotations

import re
import socket
import stat
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response

import hashlib
import shutil

from . import (agent_loops, auth, capabilities, dhcp, disk_reserve, foreign_vlan, hello,
               identity, monitor, ports, timesync)
from .db import last_disk_full, now_iso
from . import auto_reboot, backup_job
from . import raid_status

#: ‏#1000: מתי `/health` נמדד — כותרת HTTP (ISO, שעון השרת), כי הגוף הוא
#: מערך ש-`HOME`, ‏`updateAlertBadge` וטסטי node קוראים כמות שהוא; מעטפת
#: הייתה שוברת את כולם. הקונסולה קוראת אותה ב-`loadHealth` (fetch ישיר,
#: כמו `X-Journal-Search-Truncated`) ומציגה "נבדק HH:MM" מזמן השרת.
CHECKED_AT_HEADER = "X-Health-Checked-At"

BOOT_FILES = ("bootx64.efi", "grubx64.efi", "grub/grub.cfg")

#: ‏#976: הסימון של הבדיקה העצמית על בקשות `/boot/*` — הצד השולח של
#: `hello.PROBE_HEADER`, מאותו מקור אמת (שמות כותרות אינם תלויי-רישיות).
PROBE_HEADER = hello.PROBE_HEADER.decode("ascii")

#: מה שהתחנה מושכת ב-HTTP אחרי התפריט, מתוך תיקיית האתחול (#333).
#: ה-initramfs הגרפי (#32) אינו כאן: היעדרו נופל לטקסטואלי, ואינו חוסם.
BOOT_ASSETS = ("vmlinuz", "initrd.img")
NOT_APPLICABLE = "לא ישים עד שתוגדר רשת ההפצה"

#: רצפה גסה. קרנל ו-initramfs אמיתיים גדולים בהרבה, וגוף ה-404 של ‎/boot
#: הוא **תשעה** בייטים — ולכן "יש תשובה ויש גודל" אינו "יש קובץ" (#332).
MIN_ASSET_BYTES = 1 << 20


def _sha256(path) -> str:
    """‏"" כשאי-אפשר לקרוא — נבדל מ-hash שנקרא, ואינו "תואם"."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _run(cmd: list[str], *, check: bool = False) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=5, check=check).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def default_hooks() -> dict:
    return {
        "ss": lambda: _run(["ss", "-ulnp"]),
        # ‏#996: גם TCP — המאזינים של השרת עצמו (8080/8081/8443) נקראים
        # מטבלת הסוקטים, לא מההגדרה. ‏`port_listeners` (מנהל המאזינים,
        # ‏`ports.Listeners`) מוזרק מ-main.py; ‏None = אין מנהל בתהליך הזה.
        "ss_tcp": lambda: _run(["ss", "-ltnp"]),
        "port_listeners": None,
        # ‏#1013: קובץ ה-dnsmasq של המתקין — אם הוא עדיין נושא enable-tftp,
        # אין מתג ל-69 (dnsmasq מצטבר). שלושה מצבים: טקסט / "" (אין קובץ) /
        # None (לא נקרא) — ראה dhcp.read_installer_conf.
        "installer_conf": dhcp.read_installer_conf,
        "shim_src": lambda: "/usr/lib/shim/shimx64.efi.signed",
        "unit_active": lambda name: _run(["systemctl", "is-active", name]).strip(),
        # #1816: only this boot's dnsmasq messages, bounded and without a pager.
        "dnsmasq_journal": lambda: _run([
            "journalctl", "-b", "-u", "dnsmasq.service", "-t", "dnsmasq",
            "-n", "20", "--no-pager", "--quiet", "-o", "cat"], check=True),
        "timesync_status": lambda: timesync.status(timesync.default_hooks()),
        "http_get": _http_probe,
        "http_size": _http_size,
        "interfaces": dhcp.list_interfaces,
        "tftp_root": lambda: Path("/srv/tftp"),
        "udp_sender_pids": live_udp_sender_pids,
        # ‏#1074: חומת האש נקראת מ-nft, לא מההגדרה. ‏None מה-hook =
        # לא הצלחנו לבדוק; מחרוזת בלי טבלת imagectl = לא נטענה.
        "nft_ruleset": ports.read_nft_ruleset,
        # ‏#1088: מצב רשת ההפצה (‏`deploy_net.DeployState`) ושורת "כתובת
        # השרתים" (‏`(state, detail)` מ-`ifaddr.servers_nic_status`). ‏None =
        # לא הוזרק — אין שורה, לא "הכול בסדר". ‏main מזריק את שניהם.
        "deploy": None,
        "servers_nic": None,
        # ‏#1372: המארח של "עדכון ב-VM" — ‏`(state, detail)` מ-`vmhost.VmHost.health`.
        # ‏None = לא הוזרק (בדיקות, הרצה ישירה) — אין שורה; ‏main מזריק.
        "vm_host": None,
        # ‏#1972: ה-querier בכרטיס ההפצה (‏`igmp_querier.Querier.health`), ו-#1973:
        # ה-UPS (‏`ups.UpsMonitor.health`) — ‏`(state, detail)`. ‏None = לא הוזרק
        # (בדיקות, הרצה ישירה) — אין שורה; ‏main מזריק את שניהם.
        "igmp_querier": None,
        "ups": None,
        # ‏#968: זמן הפעילות של **המכונה** (‏`/proc/uptime`), ל-`/me`. ‏None =
        # לא נקרא (ווינדוס, קובץ חסר) — הקונסולה מציגה "לא נבדק", לא 0.
        "uptime": read_uptime,
        # ‏#1892: הפנוי במחיצת ה-DB (‏data_dir); ‏None = לא נמדד.
        "db_free": _db_free,
        # ‏#1989: מערכי ה-RAID — ‏/proc/mdstat הוא הראיה, ‏/etc/imagectl/raid
        # הוא מה שהמתקין יצר. ‏None מה-hook = אין קובץ; ‏OSError = לא נקרא.
        "mdstat": lambda: raid_status.read_optional(raid_status.MDSTAT_PATH),
        "raid_declared": lambda: raid_status.read_optional(raid_status.RAID_FACT_PATH),
    }


def _dnsmasq_down_detail(active: str, journal: str) -> str:
    """Use the latest diagnostic, not an old missing NIC before a later start."""
    for line in reversed(journal.splitlines()):
        message = line.strip().removeprefix("dnsmasq: ")
        # dnsmasq logs this generic footer after the actual startup error.
        if not message or message == "FAILED to start up":
            continue
        missing = re.fullmatch(r"unknown interface (\S+)", message)
        if missing:
            return f"dnsmasq down: unknown interface {missing[1]}"
        break
    detail = f"מצב: {active}"
    if not journal.strip():
        detail += " · dnsmasq journal unavailable or empty — cause not checked"
    return detail


def _db_free(data_dir) -> int | None:
    if data_dir is None:
        return None
    try:
        return shutil.disk_usage(data_dir).free
    except OSError:
        return None


#: ‏#1892: מתחת לזה — אדום. ‏1 GiB הוא נתח "ה-DB ו-/var/tmp" ברזרבת המערכת
#: (`disk_reserve`, ‏#1737); מתחת לרזרבה כולה — כתום.
DB_FREE_BAD_BYTES = 1024 ** 3


def db_space_check(free: int | None, full: dict | None) -> dict:
    """‏#1892: מחיצת ה-DB. כתיבה שכבר נכשלה ב-SQLITE_FULL אדומה גם אם
    המדידה עכשיו מראה מקום — היא הראיה, והיא מתנקה בכתיבה המוצלחת הבאה."""
    label = "מקום פנוי ל-DB"
    if full is not None:
        return check("db_space", label, "bad",
                     f"כתיבה ל-DB נכשלה כי המחיצה מלאה ({full['what']}, {full['at']})")
    if free is None:
        return check("db_space", label, "unknown", "לא ניתן למדוד את המחיצה — לא נבדק")
    shown = disk_reserve.gb(free)
    if free < DB_FREE_BAD_BYTES:
        return check("db_space", label, "bad",
                     f"נותרו {shown} — ה-DB עומד להתמלא ויפסיק לרשום")
    if free < disk_reserve.reserve():
        return check("db_space", label, "warn",
                     f"נותרו {shown}, פחות מרזרבת המערכת ({disk_reserve.gb(disk_reserve.reserve())})")
    return check("db_space", label, "ok", f"פנויים {shown}")


#: ‏#968: המחשב, לא התהליך — למפעיל "השרת פעיל X" הוא המכונה; זמן
#: התהליך היה מתאפס בכל `systemctl restart` ומטעה (הערת ה-Issue).
UPTIME_PATH = "/proc/uptime"


def read_uptime(path: str = UPTIME_PATH) -> int | None:
    """שניות מאז שהמכונה עלתה, או ``None`` כשלא נקרא — שני מצבים שונים
    (עיקרון 5): קובץ חסר/פגום אינו "0 שניות"."""
    try:
        first = Path(path).read_text(encoding="ascii").split()[0]
        return int(float(first))
    except (OSError, ValueError, IndexError):
        return None


def _probe_request(url: str) -> urllib.request.Request:
    """הבקשה של הבדיקה העצמית — מסומנת (‏#976).

    בלי הסימון השרת רשם את הבדיקה של עצמו כמכונה: ‏`net_seen` עם
    `127.0.0.1`, פירור `menu` שאיפס שביל אמיתי, והתקן "לא רשום"
    `00:00:00:00:00:00`. הסימון מכובד רק כשהפונה הוא השרת עצמו
    (‏`hello.self_probe`), ולכן הוא אינו דרך לאתחל בלי להירשם.
    """
    return urllib.request.Request(url, headers={PROBE_HEADER: "1"})


def _http_probe(url: str) -> int | None:
    try:
        with urllib.request.urlopen(_probe_request(url), timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def _http_size(url: str) -> tuple[int | None, int | None]:
    """קוד תשובה **וגודל מוצהר**, בלי לקרוא את הגוף — initrd הוא עשרות
    מגהבייט, ומסך בריאות אינו מושך אותם. גודל שלא הוצהר חוזר `None`,
    שאינו 0 ואינו "בסדר"."""
    try:
        with urllib.request.urlopen(_probe_request(url), timeout=5) as response:
            length = response.headers.get("Content-Length")
            return response.status, int(length) if str(length).isdigit() else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return None, None


def boot_asset_problems(probe, server_base: str) -> list[str]:
    """‏vmlinuz ו-initrd.img — כפי שהתחנה מושכת אותם, לא כפי שהם בדיסק.

    בהתקנה נקייה (#332) שניהם החזירו 404 בזמן שהשורה הייתה ירוקה.
    הראיה כאן חיובית ומורכבת משניים — **200 וגם** גודל שאינו יכול
    להיות גוף שגיאה — ומי מהם נכשל נאמר בשמו.
    """
    problems = []
    for name in BOOT_ASSETS:
        status, size = probe(server_base.rstrip("/") + "/boot/" + name)
        if status is None:
            problems.append(f"{name}: הבדיקה עצמה לא רצה (השרת לא ענה)")
        elif status != 200:
            problems.append(f"{name}: {status} מ-/boot")
        elif size is None:
            problems.append(f"{name}: 200 בלי גודל מוצהר — הבדיקה לא רצה")
        elif size < MIN_ASSET_BYTES:
            problems.append(f"{name}: {size} בייטים בלבד — אינו קובץ אתחול")
    return problems


def port_owner(ss_output: str, port: int) -> str | None:
    """מי מאזין על פורט UDP — שם התהליך מתוך ss -ulnp, או None."""
    for line in ss_output.splitlines():
        if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
            continue
        if '"' in line:
            return line.split('"')[1]
        return "?"
    return None


def check(check_id: str, label: str, state: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "state": state, "detail": detail}


def time_sync_check(status: dict | str, legacy_sync: str | None = None) -> dict:
    # Compatibility for callers from before #1773; the live route uses the
    # structured measurement above, including its explicit unknown state.
    if isinstance(status, str):
        status = {"synchronized": (legacy_sync == "yes" and status == "active") if legacy_sync else None,
                  "server": "chrony" if status == "active" else None,
                  "offset_ms": None}
    # #1814: source · synchronized or not · offset. The offset's size decides too: over
    # 1 s chrony should already have stepped (makestep 1.0 -1), and from 30 s TOTP codes
    # no longer match (±30 s) -- every MFA login fails, so that is `bad`, not `warn`.
    synchronized = status.get("synchronized")
    if synchronized is None:
        return check("time_sync", "שעון", "off", "לא נבדק")
    offset = status.get("offset_ms")
    parts = [f"מקור {status.get('server') or 'לא ידוע'}", "מסונכרן" if synchronized else "לא מסונכרן"]
    if offset is None:
        parts.append("סטייה לא נמדדה")
    elif abs(offset) >= 1000:
        parts.append(f"השעון {'מקדים' if offset > 0 else 'מאחר'} ב-{abs(offset) / 1000:.0f} ש'")
    else:
        parts.append(f"סטייה {offset:+.3f} ms")
    if offset is not None and abs(offset) >= 30_000:
        return check("time_sync", "שעון", "bad", " · ".join(parts + ["קודי MFA לא יתקבלו"]))
    steady = synchronized and (offset is None or abs(offset) < 1000)
    return check("time_sync", "שעון", "ok" if steady else "warn", " · ".join(parts))


def live_udp_sender_pids() -> list[int] | None:
    """PIDs של udp-sender חיים. ‏None = לא הצלחנו לסרוק (#439)."""
    root = Path("/proc")
    if not root.is_dir():
        return None
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    found: list[int] = []
    saw = False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        saw = True
        if comm == "udp-sender":
            found.append(int(entry.name))
    return sorted(found) if saw else None


def _hook_pids(hooks: dict) -> list[int] | None:
    getter = hooks.get("udp_sender_pids")
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 — בדיקה שנפלה אינה "לא רץ"
        return None


def _send_in_progress(ctx) -> bool:
    sender = getattr(ctx, "sender", None)
    try:
        status = sender.status() if sender is not None else None
    except Exception:  # noqa: BLE001 — status שנפל אינו "אין סבב"
        status = None
    if status and status.get("state") in ("starting", "sending"):
        return True
    try:
        return ctx.conn.execute(
            "SELECT 1 FROM sessions WHERE state = 'running' LIMIT 1"
        ).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def sender_check(pids: list[int] | None, live_round: bool) -> dict:
    """רץ עם סבב → warn; רץ בלי סבב → bad; לא רץ → ok. לא הורג (#439).

    ‏None (הבדיקה לא רצה) אינו ok: "לא רץ" הוא הירוק, ואי-בדיקה שנראית
    כמו ירוק היא בדיוק עיקרון 5.
    """
    label = "משדר udp-sender"
    if pids is None:
        return check("udp_sender", label, "bad",
                     "לא הצלחנו לבדוק אם udp-sender רץ — אין לדעת אם נשאר יתום")
    if not pids:
        return check("udp_sender", label, "ok", "לא רץ")
    shown = ", ".join(f"PID {p}" for p in pids)
    if live_round:
        return check("udp_sender", label, "warn",
                     f"רץ ({shown}) — יש סבב פתוח")
    return check("udp_sender", label, "bad",
                 f"רץ בלי סבב ({shown}) — יתום אחרי סגירה, יש לעצור אותו")


def tftp_file_problems(root: Path) -> list[str]:
    """#1817: dnsmasq needs o+r on files and o+x along the whole path.

    The server may run as root: its own access is not evidence for dnsmasq.
    Inspect both the requested path and symlink targets; never change modes.
    """
    problems, missing = [], []
    checked_dirs = set()
    for name in BOOT_FILES:
        path = (root / name).absolute()
        directories = list(reversed(path.parents))
        try:
            resolved = path.resolve(strict=True)
            directories += list(reversed(resolved.parents))
        except FileNotFoundError:
            missing.append(name)     # the pre-#1817 message, by name
            continue
        except (OSError, RuntimeError) as exc:
            problems.append(f"לא ניתן לבדוק {path}: {exc}")
        for directory in directories:
            if directory in checked_dirs:
                continue
            checked_dirs.add(directory)
            try:
                mode = directory.stat().st_mode
            except OSError as exc:
                problems.append(f"לא ניתן לבדוק {directory}: {exc}")
                continue
            if not stat.S_ISDIR(mode) or not mode & stat.S_IXOTH:
                problems.append(
                    f"{directory} (mode {stat.S_IMODE(mode):04o}): "
                    "נדרשת תיקייה עם o+x עבור dnsmasq")
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            problems.append(f"לא ניתן לבדוק {path}: {exc}")
            continue
        if not stat.S_ISREG(mode) or not mode & stat.S_IROTH:
            problems.append(
                f"{path} (mode {stat.S_IMODE(mode):04o}): "
                "נדרש קובץ רגיל עם o+r עבור dnsmasq")
    return ([f"חסרים ב-{root}: {', '.join(missing)}"] if missing else []) + problems


def collect(ctx, hooks: dict, server_base: str) -> list[dict]:
    results = []
    reboot_detail = auto_reboot.health_detail(ctx)
    results.append(check("auto_reboot", "הפעלה מחדש הבאה",
                         "bad" if reboot_detail == "המתזמן אינו רץ" else "not_applicable",
                         reboot_detail))
    ss_out = hooks["ss"]()

    if hooks.get("time_synchronized") is not None:  # legacy injected test hook
        sync = {"synchronized": hooks["time_synchronized"]() == "yes",
                "server": "chrony" if hooks["unit_active"]("chrony") == "active" else None,
                "offset_ms": None}
    else:
        sync = hooks["timesync_status"]() if hooks.get("timesync_status") else {"synchronized": None}
    results.append(time_sync_check(sync))
    results.append(db_space_check(hooks["db_free"](getattr(ctx, "data_dir", None)),
                                  last_disk_full()))
    # ‏#1895 (נדב 22/10): גיבוי המסד — כתום עד שנבחר יעד, "ישן" אחרי שעתיים, אדום על כשל.
    results.append(backup_job.health_check(ctx.conn, getattr(ctx, "data_dir", None),
                                           hooks["unit_active"](backup_job.TIMER_UNIT)))

    # ‏#1088: רשת ההפצה — טרם הוגדרה היא מצב **מוצהר** אחרי התקנה נקייה,
    # לא כשל: השורה אומרת מה לעשות, ובדיקות ה-PXE שתלויות בה (TFTP,
    # ‏dnsmasq, השרת בכתובת ההפצה) הופכות ל"לא רלוונטי עדיין" ולא לאדום
    # שמכסה על הבעיה האמיתית. כשהיא מוגדרת — אין שורה, והבדיקות כרגיל.
    deploy_state = hooks.get("deploy")() if hooks.get("deploy") else None
    unconfigured = deploy_state is not None and not deploy_state.configured
    if unconfigured:
        results.append(check("deploy_net", "הגדר את רשת ההפצה", "warn",
                             deploy_state.public()["hint"]))

    # ‏#1088: כתובת כרטיס השרתים — DHCP ומתי החכירה פגה, או סטטי.
    servers_nic = hooks.get("servers_nic")() if hooks.get("servers_nic") else None
    if servers_nic is not None:
        results.append(check("servers_nic", "כתובת השרתים", *servers_nic))

    # פורט 67 — DHCP. פנוי זה מצב לגיטימי (עוד לא הוגדר מהקונסולה).
    owner = port_owner(ss_out, 67) if ss_out else None
    if unconfigured:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "not_applicable",
                             NOT_APPLICABLE))
    elif not ss_out:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "off",
                             "אי אפשר לבדוק כאן (ss לא זמין)"))
    elif owner is None:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "warn",
                             "אף אחד לא מאזין — DHCP עוד לא הודלק מלשונית הרשת"))
    elif owner == "dnsmasq":
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "ok", "dnsmasq מאזין"))
    else:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "bad",
                             f"תפוס על ידי {owner} — יתנגש עם dnsmasq"))

    # פורט 69 — TFTP. בלעדיו אין שרשרת אתחול.
    owner = port_owner(ss_out, 69) if ss_out else None
    if unconfigured:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "not_applicable",
                             NOT_APPLICABLE))
    elif not ss_out:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "off",
                             "אי אפשר לבדוק כאן (ss לא זמין)"))
    elif owner is None:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "bad",
                             "אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE"))
    elif owner == "dnsmasq":
        results.append(check("tftp_port", "פורט 69 (TFTP)", "ok", "dnsmasq מגיש"))
    else:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "warn",
                             f"מוגש על ידי {owner}, לא על ידי dnsmasq"))

    # dnsmasq עצמו.
    active = hooks["unit_active"]("dnsmasq")
    results.append(
        check("dnsmasq", "שירות dnsmasq", "not_applicable", NOT_APPLICABLE)
        if unconfigured
        else check("dnsmasq", "שירות dnsmasq", "ok", "רץ") if active == "active"
        else check("dnsmasq", "שירות dnsmasq", "off",
                   "אי אפשר לבדוק כאן (systemctl לא זמין)") if not active
        else check("dnsmasq", "שירות dnsmasq", "bad",
                   _dnsmasq_down_detail(active, hooks.get("dnsmasq_journal", lambda: "")())))

    # שרשרת האתחול, משני קצותיה: הקבצים על שורש ה-TFTP, ומיד אחריהם
    # הקרנל וה-initramfs כפי שהתחנה מושכת אותם ב-HTTP (#333).
    root = hooks["tftp_root"]()
    problems = [] if unconfigured else tftp_file_problems(root)
    if not unconfigured:
        problems += boot_asset_problems(hooks["http_size"], server_base)
    if unconfigured:
        results.append(check("boot_files", "קבצי האתחול", "not_applicable",
                             NOT_APPLICABLE))
    elif not problems:
        results.append(check("boot_files", "קבצי האתחול", "ok",
                             f"shim, GRUB והתפריט הקבוע נמצאים ב-{root}, "
                             "ו-vmlinuz ו-initrd.img נמשכו מ-/boot בגודל מלא"))
    else:
        results.append(check("boot_files", "קבצי האתחול", "bad",
                             " · ".join(problems) + " — הריצו את המתקין"))

    # ‏#329: המתקין מעתיק את ה-shim פעם אחת ולא מעדכן. מיקרוסופט
    # דוחפת עדכוני SBAT שמבטלים shim ישן, ואז **כל הצי** מפסיק לעלות
    # ב-PXE בבת אחת — בלי שנגענו בכלום. ‏`apt upgrade` אינו מספיק:
    # הוא מעדכן את /usr/lib/shim, לא את העותק כאן.
    src, tftp = hooks["shim_src"](), root / "bootx64.efi"
    a, b = _sha256(src), _sha256(tftp)
    if a and b and a == b:
        results.append(check("shim_fresh", "עדכניות ה-shim", "ok",
                             "העותק ב-TFTP תואם למה שהמערכת מספקת"))
    elif a and b:
        results.append(check("shim_fresh", "עדכניות ה-shim", "bad",
                             f"{tftp} ישן מ-{src} — הריצו את המתקין מחדש"))
    else:
        results.append(check("shim_fresh", "עדכניות ה-shim", "unknown",
                             f"לא ניתן לקרוא {src if not a else tftp} — לא נבדק"))

    # השרת עצמו, בכתובת שהלקוחות רואים. הקונסולה שקוראת את זה כבר מדברת
    # איתנו — הבדיקה היא שהכתובת הציבורית (זו שב-GRUB) אכן עונה.
    # ‏#996: פורט שהמפעיל כיבה בדף הפורטים הוא "כבוי", לא "לא עונה" —
    # שני מצבים שונים, ורק השני הוא תקלה.
    if unconfigured:
        status = "unconfigured"
    elif not ports.enabled(ctx.conn, "http_boot"):
        status = "disabled"
    else:
        status = hooks["http_get"](server_base.rstrip("/") + "/boot/menu?mac=00:00:00:00:00:00")
    if status == "unconfigured":
        results.append(check("server", "השרת בכתובת ההפצה", "not_applicable",
                             NOT_APPLICABLE))
    elif status == "disabled":
        results.append(check("server", "השרת בכתובת ההפצה", "off",
                             "פורט הסוכן (8080) כבוי על ידי המפעיל בדף הפורטים — "
                             "תחנות לא יגיעו לתפריט עד שיודלק"))
    elif status == 200:
        results.append(check("server", "השרת בכתובת ההפצה", "ok",
                             f"{server_base} עונה על תפריט האתחול"))
    elif status is None:
        results.append(check("server", "השרת בכתובת ההפצה", "bad",
                             f"{server_base} לא עונה — מחשבים לא יגיעו לתפריט"))
    else:
        results.append(check("server", "השרת בכתובת ההפצה", "warn",
                             f"{server_base} החזיר {status}"))

    # udp-sender חי בלי סבב הוא יתום שתופס את פורטי ההפצה (#439).
    # הבדיקה מזהה ומראה — היא אינה הורגת: ההריגה שייכת לנתיב הסגירה.
    results.append(sender_check(_hook_pids(hooks), _send_in_progress(ctx)))

    # כרטיסי הרשת — כמה מחוברים, וכמה מגישים DHCP (לפי ההגדרות השמורות).
    nics = hooks["interfaces"]()
    up = [n["name"] for n in nics if n.get("state") == "up"]
    serving = [
        row["key"].removeprefix(dhcp.SETTING_PREFIX)
        for row in ctx.conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (dhcp.SETTING_PREFIX + "%",),
        )
        if '"enabled": true' in row["value"] or '"proxy": true' in row["value"]
    ]
    if not nics:
        results.append(check("nics", "כרטיסי רשת", "bad", "לא נמצאו כרטיסים"))
    elif not up:
        results.append(check("nics", "כרטיסי רשת", "warn",
                             "אף כרטיס לא מחובר (כבל?)"))
    else:
        detail = f"מחוברים: {', '.join(up)}"
        detail += f" · DHCP פעיל על: {', '.join(serving)}" if serving \
            else " · DHCP לא הודלק על אף כרטיס"
        results.append(check("nics", "כרטיסי רשת", "ok", detail))

    # ‏#1810: כרטיס ההפצה נשמר **כשם** (nft, ‏dnsmasq, ‏udp-sender). שם שהשתנה
    # (שדרוג דביאן, vNIC חדש, כרטיס שהוחלף) — הכול נטען ושום דבר לא נתפס.
    deploy_if = deploy_state.interface if deploy_state is not None         and deploy_state.configured else None
    names = [n["name"] for n in nics]
    if deploy_if and not nics:
        results.append(check("deploy_nic", "כרטיס ההפצה", "unknown",
                             f"רשימת הכרטיסים ריקה — לא נבדק אם {deploy_if} קיים"))
    elif deploy_if and deploy_if not in names:
        results.append(check("deploy_nic", "כרטיס ההפצה", "bad",
                             f"כרטיס ההפצה {deploy_if} לא קיים במערכת (יש: "
                             f"{', '.join(names)}) — DHCP, TFTP וחומת האש מפנים "
                             "לשם שאינו קיים; בחרו כרטיס בלשונית רשת הפצה"))
    elif deploy_if:
        results.append(check("deploy_nic", "כרטיס ההפצה", "ok",
                             f"{deploy_if} קיים במערכת"))

    # ‏#1074: חומת האש שהמתקין כותב — שלושה מצבים, לא "חסר = כבוי".
    fw_state, fw_detail = ports.firewall_status(hooks["nft_ruleset"](), names)
    results.append(check("firewall", "חומת אש", fw_state, fw_detail))

    # ‏#1972: querier ברשת ההפצה — פעיל / יש querier אחר / כבוי. ‏#1973: מצב ה-UPS.
    if hooks.get("igmp_querier"):
        results.append(check("igmp_querier", "IGMP querier", *hooks["igmp_querier"]()))
    if hooks.get("ups"):
        results.append(check("ups", "UPS", *hooks["ups"]()))

    # ‏#1372: מארח שאינו מוכן ל-VM הוא אזהרה בשם — לא שקט ירוק אחרי עדכון.
    if hooks.get("vm_host"):
        results.append(check("vm_host", "עדכון ב-VM", *hooks["vm_host"]()))

    # ‏#1846: האבדן ברשת ההפצה מול הבסיס שלה — שורה לכל כרטיס הפצה שנמדד עליו גל.
    from . import learn_netloss as _learn_netloss
    results.extend(_learn_netloss.health_checks(ctx.conn))
    # ‏#1847: מכונה שלא מתעוררת מ-WoL — רצף של הערות בלי תשובה.
    from . import learn_wol as _learn_wol
    results.extend(_learn_wol.health_checks(ctx.conn))
    # ‏#1848: שלב אתחול שהתארך פי 2 מהבסיס של המכונה, ולפני/אחרי עדכון.
    from . import learn_boot as _learn_boot
    results.extend(_learn_boot.health_checks(ctx.conn))
    # ‏#1884: דיסק הספרייה בגל האחרון — קצב ולחץ I/O.
    from . import server_io as _server_io
    results.extend(_server_io.health_checks(ctx.conn))
    # ‏#1886: המקום הפנוי לאורך זמן — "מתמלא בעוד ~N ימים".
    from . import storage_history as _storage_history
    results.extend(_storage_history.health_checks(ctx.conn))
    # ‏#1889: סיכום שבועי של ההמתנה למפעיל — המלצה לאדם, לא פעולה.
    from . import learn_waits as _learn_waits
    results.extend(_learn_waits.health_checks(ctx.conn))

    # #1066: שורה לכל מיקום אחסון — מחובר / לא נגיש / מנותק / לא נבדק.
    from . import storage_locations as _storage_locations
    results.extend(_storage_locations.health_checks(ctx))
    # ‏#1989: שורה לכל מערך RAID — תקין / פגוע / בבנייה מחדש (עם אחוז).
    results.extend(raid_status.health_checks(hooks.get("mdstat"), hooks.get("raid_declared")))

    # ‏#855: שומר הזהות — מתג כבוי הוא מצב מוצהר שנאמר כאן, וקובץ חכירות
    # שאינו נקרא הוא "כל hello מסורב", לא שקט.
    if unconfigured:
        results.append(check("identity", "זהות מכונה", "not_applicable",
                             NOT_APPLICABLE))
    else:
        results.append(check("identity", "זהות מכונה",
                             *identity.health_status(ctx.conn, getattr(ctx, "leases", None))))

    # ואחרונות, כי אורכן משתנה: מי נופל לסוכן בלולאה עכשיו (#112), ומי
    # מדבר עם השרת מרשת שאינה וילן ההפצה (#137). שתי רשימות נפרדות —
    # פנייה מרשת אחרת אינה לולאה, ואינה נספרת כאחת.
    try:
        loops = agent_loops.current(ctx.conn)
    except Exception:  # noqa: BLE001 — שאילתה שנפלה אינה "אין לולאות"
        loops = None
    results.extend(agent_loops.loop_checks(loops))

    try:
        strangers = foreign_vlan.current(ctx.conn)
    except Exception:  # noqa: BLE001 — שאילתה שנפלה אינה "אף אחד לא פנה"
        strangers = None
    results.extend(foreign_vlan.vlan_checks(
        strangers, urlsplit(server_base).hostname or server_base))

    return results


def _last_seen(seconds: int) -> str:
    """שימוש: `agent_loops.loop_checks` ו-`foreign_vlan.vlan_checks`, שתיהן
    מייבאות מכאן בגוף הפונקציה (לא בראש הקובץ) כדי לא ליצור מעגל ייבוא —
    שני המודולים האלה כבר מיובאים כאן, בראש הקובץ."""
    if seconds < 60:
        return "נראה לאחרונה לפני פחות מדקה"
    return f"נראה לאחרונה לפני {seconds // 60} דק'"


#: הפורטים של השרת עצמו כשאין מנהל מאזינים בתהליך (‏create_app של
#: הבדיקות): מה ש-main.py קושר כברירת מחדל. עם מנהל (‏#996) הפורט
#: והכתובות נקראים ממנו, ומה שבאמת מאזין — מטבלת הסוקטים.
DEFAULT_OWN_PORTS = {"http_boot": 8080, "http_console": 8081,
                     "interserver": 8443}


def _port(port_id: str, name: str, port: str, proto: str, desc: str,
          target: str, state: str, detail: str, note: str, *,
          enabled: bool | None = None, listening: bool | None = None,
          bind: list[str] | tuple | None = (), toggle: str = "none",
          toggle_url: str | None = None, confirm_word: str | None = None,
          confirm_when: str | None = None, off_means: str = "",
          **extra) -> dict:
    """שורה ב-/ports. ‏#996 הוסיף את המתג: ‏`enabled` (המתג השמור; None =
    אין מתג), ‏`listening` (נמדד: True/False, ‏None = לא נקרא כאן),
    ‏`bind` (כתובות ההאזנה מטבלת הסוקטים; ‏#1039: ‏None = הטבלה לא
    נקראה ≠ ‏`[]` = נקראה ואף אחד לא מאזין; שורה בלי סוקט בשרת לא מעבירה
    ומקבלת `[]`), ‏`toggle` (`api`/`confirm`/
    ‏`none`), ‏`toggle_url` (איפה המתג חי), ‏`confirm_word`/`confirm_when`
    (מה מקלידים ומתי: `on`/`off`), ‏`off_means`."""
    # אזהרה לפני כיבוי לכל פורט עם מתג (נדב 19/09) — מהטבלה, אלא אם השורה
    # הביאה משלה (TFTP במצב שדרוג, למשל).
    extra.setdefault("warning_he", ports.warning_for(port_id) if toggle != "none" else None)
    return {"id": port_id, "name": name, "port": port, "proto": proto,
            "desc": desc, "target": target, "state": state, "detail": detail,
            "note": note, "enabled": enabled, "listening": listening,
            "bind": None if bind is None else list(bind), "toggle": toggle,
            "toggle_url": toggle_url, "confirm_word": confirm_word,
            "confirm_when": confirm_when, "off_means": off_means, **extra}


def _dhcp_flags(ctx) -> list[dict]:
    """‏(שם, enabled, proxy) לכל כרטיס עם הגדרת DHCP שמורה — אותה קריאה
    כמו `collect`, כדי ששני המסכים יסכימו מי משרת."""
    rows = []
    for row in ctx.conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (dhcp.SETTING_PREFIX + "%",)):
        cfg = dhcp.InterfaceConfig.from_json(
            row["key"].removeprefix(dhcp.SETTING_PREFIX), row["value"])
        rows.append({"name": cfg.name, "enabled": cfg.enabled, "proxy": cfg.proxy})
    return rows


def _own_row(ctx, hooks: dict, port_id: str, ss_tcp: str, *,
             name: str, desc: str, target: str, note: str) -> dict:
    """שורה של מאזין של השרת עצמו (‏#996): הפורט מהמנהל (או ברירת המחדל),
    ההאזנה מטבלת הסוקטים, המתג מה-DB — ושלושת המצבים נצבעים בנפרד."""
    listeners = hooks.get("port_listeners")
    registered = listeners is not None and port_id in listeners.ids()
    port = listeners.spec(port_id)["port"] if registered else DEFAULT_OWN_PORTS[port_id]
    bind = ports.bind_addresses(ss_tcp, port)
    listening = None if bind is None else bool(bind)
    toggle, off_means = ports.OWN[port_id]
    if port_id == "interserver" and not registered:
        # ‏8443 עולה על משני עם --interserver-host, ועל ראשי שהקונסולה שלו על
        # כרטיס שרתים ("אתה שם?" בלבד, #1310); בלעדיהם אין מה לכבות.
        state, detail = ports.measured_state(None, listening)
        return _port(port_id, name, str(port), "tcp", desc, target, state,
                     detail + " · לא מוגדר בשרת הזה (שרת משנה: עם --interserver-host; "
                     "ראשי: כשהקונסולה על כרטיס שרתים, #1310)", note,
                     listening=listening, bind=bind, off_means=off_means)
    flag = ports.enabled(ctx.conn, port_id)
    state, detail = ports.measured_state(flag, listening)
    return _port(port_id, name, str(port), "tcp", desc, target, state, detail,
                 note, enabled=flag, listening=listening, bind=bind,
                 toggle=toggle, toggle_url=f"/api/console/ports/{port_id}",
                 confirm_word=ports.server_name(ctx.conn) if toggle == "confirm" else None,
                 confirm_when="off" if toggle == "confirm" else None,
                 off_means=off_means)


def ports_snapshot(ctx, hooks: dict, server_base: str) -> list[dict]:
    """‏#822: מסך "פורטים" בקונסולה. כל שורה נקראת מהשרת — לא רשימה
    קבועה ב-JS — ומצב "לא נקרא" נאמר בפירוש כשטבלת הסוקטים לא נקראה
    (עיקרון 5: לא לצייר ירוק על מה שלא נבדק).

    ‏#996: לכל שורה מתג — ‏`enabled`/`bind`/`toggle`/`toggle_url`/
    ‏`off_means` (ראה `_port`) — והשורות שחסרו: DHCP 67, בין-שרתים
    8443. ‏`listening` ו-`bind` באים מ-`ss` (‏udp
    ו-tcp), לא מההגדרה."""
    ss_out = hooks["ss"]()
    ss_tcp = hooks["ss_tcp"]()
    entries = []
    dhcp_flags = _dhcp_flags(ctx)

    # ‏TFTP 69 (‏#1013): המתג נשמר כ-`port:tftp` ומוחל דרך ה-render של
    # dnsmasq; `ports.tftp_switch` מחליט אם יש מתג בכלל (קובץ המתקין).
    # המצב הנמדד נשאר `ss`: "כבוי" (המתג) ≠ "לא מאזין" ≠ "לא נקרא".
    tftp_bind = ports.bind_addresses(ss_out, 69)
    owner = port_owner(ss_out, 69) if ss_out else None
    tftp_note = "לפתוח ב-FW: UDP 69 מוילן ההפצה לשרת"
    switch = ports.tftp_switch(ctx.conn, hooks["installer_conf"]())
    flag = switch["enabled"]
    if not ss_out:
        listening, state, detail = None, "unknown",             "טבלת הסוקטים לא נקראה (ss לא זמין) — לא ידוע אם מאזין"
    elif owner is None:
        listening = False
        state, detail = (("off", "כבוי על ידי המפעיל — לא מאזין") if flag is False
                         else ("bad", "אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE"))
    elif owner == "dnsmasq":
        listening = True
        state, detail = (("bad", "המתג כבוי אבל dnsmasq עדיין מגיש — הכיבוי לא תפס")
                         if flag is False else ("ok", "dnsmasq מגיש"))
    else:
        listening, state, detail = True, "warn", f"מוגש על ידי {owner}, לא על ידי dnsmasq"
    entries.append(_port("tftp", "TFTP", "69", "udp",
        "bootloader — shim/GRUB והתפריט", "תחנות (PXE)", state, detail, tftp_note,
        listening=listening, bind=tftp_bind, **switch))

    # ‏DHCP 67 — המתג חי ב-PUT /net/interfaces/{n} (enabled, +confirm = שם
    # הכרטיס); כאן רק החיווי וההפניה. proxy גם הוא מאזין על 67.
    dhcp_bind = ports.bind_addresses(ss_out, 67)
    dhcp_on = any(f["enabled"] or f["proxy"] for f in dhcp_flags)
    owner = port_owner(ss_out, 67) if ss_out else None
    if not ss_out:
        d_state, d_detail = "unknown", "טבלת הסוקטים לא נקראה (ss לא זמין) — לא ידוע אם מאזין"
    elif owner is not None and owner != "dnsmasq":
        d_state, d_detail = "bad", f"תפוס על ידי {owner} — יתנגש עם dnsmasq"
    else:
        d_state, d_detail = ports.measured_state(dhcp_on, owner is not None)
    entries.append(_port("dhcp", "DHCP", "67", "udp",
        "חלוקת כתובות ו-dhcp-boot לתחנות (dnsmasq)", "תחנות (PXE)",
        d_state, d_detail, "לפתוח ב-FW: UDP 67/68 בתוך וילן ההפצה בלבד — ברודקאסט",
        enabled=dhcp_on, listening=None if not ss_out else owner is not None,
        bind=dhcp_bind, toggle="confirm", toggle_url=ports.ELSEWHERE["dhcp"],
        confirm_when="on",
        off_means="תחנות לא יקבלו כתובת ולא dhcp-boot — אין PXE מהשרת הזה",
        interfaces=dhcp_flags))

    http = _own_row(ctx, hooks, "http_boot", ss_tcp, name="HTTP",
        desc="אתחול (boot/vmlinuz/initrd) ו-API הסוכן", target="תחנות, סוכן",
        note="לפתוח ב-FW: TCP 8080 מוילן ההפצה לשרת")
    if http["state"] == "ok":
        # מאזין ודלוק — ועכשיו גם עונה? הראיה של #822 נשארת.
        status = hooks["http_get"](
            server_base.rstrip("/") + "/boot/menu?mac=00:00:00:00:00:00")
        if status == 200:
            http["detail"] = f"{server_base} עונה על תפריט האתחול"
        elif status is None:
            http["state"], http["detail"] = "bad", f"{server_base} לא עונה"
        else:
            http["state"], http["detail"] = "warn", f"{server_base} החזיר {status}"
    entries.append(http)

    # ‏#703 (tracer 5): הקונסולה מוגשת ב-HTTPS בלבד (תעודה חתומה-עצמית).
    entries.append(_own_row(ctx, hooks, "http_console", ss_tcp, name="HTTPS",
        desc="קונסולת הניהול (TLS, תעודה עצמית)", target="דפדפן (מנהל)",
        note="לפתוח ב-FW: TCP 8081 (HTTPS) מתחנת הניהול בלבד — לא לוילן הכיתות"))

    # ‏#1303 (נדב 12/10): ‏PXE proxy שייך לכיתות (v1.2) — ‏`PUT /net/interfaces` מסרב לו ב-409
    # כשהן כבויות. ב-v1 אין שורת 4011 בדף הפורטים (שארית מוצגת של יכולת שאין).
    if capabilities.classrooms():
        proxy_bind = ports.bind_addresses(ss_out, 4011)
        proxy_on = any(f["proxy"] for f in dhcp_flags)
        p_state, p_detail = ports.measured_state(
            proxy_on, None if proxy_bind is None else bool(proxy_bind))
        entries.append(_port("pxe_proxy", "PXE", "4011", "udp", "PXE proxy",
            "תחנות", p_state, p_detail, "לפתוח ב-FW: UDP 4011 מוילן ההפצה לשרת",
            enabled=proxy_on, listening=None if proxy_bind is None else bool(proxy_bind),
            bind=proxy_bind, toggle="api", toggle_url=ports.ELSEWHERE["pxe_proxy"],
            off_means="ברשת עם DHCP זר תחנות לא יקבלו הפניית PXE מהשרת הזה",
            interfaces=dhcp_flags))

    sender = sender_check(_hook_pids(hooks), _send_in_progress(ctx))
    entries.append(_port("multicast", "Multicast", "9000–9001", "udp",
        "שידור אימג׳ (fanout)", "תחנות", sender["state"], sender["detail"],
        "לפתוח ב-FW: UDP 9000–9001 בתוך וילן ההפצה בלבד",
        off_means="אין מתג: זה udp-sender של סבב פתוח, לא מאזין קבוע — "
                  "עצירת סבב היא הכיבוי (מאחורי הקלדת שם)"))

    monitor_on = monitor.stations_enabled(ctx.conn)
    entries.append(_port("monitor", "Monitor (RFB)", str(monitor.MONITOR_PORT),
        "tcp", "צפייה מרחוק במחשבי בנייה/שיכפול (#690)", "תחנות (build/cloner)",
        "warn" if monitor_on else "off",
        ("המתג monitor:stations דלוק — כל תחנה שעולה מפעילה שירות RFB"
         if monitor_on else
         "המתג monitor:stations כבוי — אף תחנה לא מפעילה שירות צפייה"),
        f"לפתוח ב-FW: TCP {monitor.MONITOR_PORT} מהשרת לוילן ההפצה — "
        "רק כשהמתג דלוק",
        enabled=monitor_on, toggle="confirm", toggle_url=ports.ELSEWHERE["monitor"],
        confirm_word=monitor.MONITOR_CONFIRM, confirm_when="on",
        off_means="אין צפייה/שליטה מרחוק במחשבי בנייה ושיכפול; אף תחנה לא "
                  "מפעילה שירות RFB"))

    ntp_bind = ports.bind_addresses(ss_out, 123)
    listening = None if ntp_bind is None else bool(ntp_bind)
    nic = getattr(ctx, "nic_services", None)
    ntp_nics = nic.current_plan()["nics"]["ntp"] if nic is not None else []
    state, detail = ports.measured_state(bool(ntp_nics), listening)
    entries.append(_port("ntp", "שרת זמן (NTP)", "123", "udp",
        "השרת מספק שעה למחשבים ברשת", "מחשבים ברשתות שנבחרו",
        state, detail, "UDP 123 נכנס רק בכרטיסים שבהם השירות דלוק",
        enabled=bool(ntp_nics), listening=listening, bind=ntp_bind,
        toggle="api", toggle_url="/api/console/net/services/{name}",
        off_means="השרת לא מספק שעה", interfaces=ntp_nics))

    entries.append(_own_row(ctx, hooks, "interserver", ss_tcp, name="HTTPS (mTLS)",
        desc="רישום וסנכרון של שרתי משנה (#740)", target="שרת ראשי/שרת משנה",
        note="לפתוח ב-FW: TCP 8443 בין השרתים בלבד"))

    ruleset = hooks["nft_ruleset"]()
    fw_state, fw_detail = ports.firewall_status(
        ruleset, [n["name"] for n in hooks["interfaces"]()])   # ‏#1810
    entries.append(_port("firewall", "חומת אש", "—", "nft",
        "מדיניות nftables לפי וילן שהמתקין כותב (#1074)", "השרת (host)",
        fw_state, fw_detail,
        "נכתבת בהתקנה; שדרוג אינו מפעיל אותה. --no-firewall מדלג",
        toggle="none",
        off_means="אין מתג בזמן ריצה: הכללים נכתבים בהתקנה "
                  "(install/nftables-rules.sh). כיבוי = nft flush / "
                  "systemctl stop nftables — מחוץ לקונסולה"))

    # ‏#1267: לכל שורה — לאיזו רשת היא שייכת; ולשורות שחוצות רשתות (ניהול,
    # בין שרתים) — מה טבלת imagectl של השרת אומרת על הפורט הזה. ברשת ההפצה
    # השרת מנהל את הפורטים ואין חומת אש של המכללה — אין שם ערך.
    for row in entries:
        row["network"] = ports.network_of(row["id"])
        row["firewall"], row["firewall_detail"] = (
            ports.port_firewall(ruleset, row["proto"], row["port"])
            if row["network"] in ("mgmt", "interserver", "per_nic") else (None, None))
    return entries


def create_health_router(ctx, server_base: str, hooks: dict | None = None,
                         dnsmasq_apply=None, version=None) -> APIRouter:
    """‏`dnsmasq_apply(what, user_id)` (‏#1013): מה שמתג ה-TFTP מפעיל —
    ‏`console_dhcp.apply_dnsmasq` עם ה-hooks של ה-DHCP; ‏None = אין.
    ‏`version()` (‏#1131): תג ה-git של העץ, אותו מקור כמו `/me` — ל-`/health/live`;
    ‏#1159: מחזיר ``update.VersionRead`` — ``update.read_version``."""
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)
    hooks = {**default_hooks(), **(hooks or {})}

    @router.get("/health/live")
    def live():
        """‏#1131: liveness **ללא הזדהות** — המתקין (לפני שהוא מפעיל חומת אש)
        ו-`tools/server-upgrade.sh` (אחרי restart) שואלים כאן מ-loopback אם
        השרת החדש עונה, ואיזו גרסה רצה. רק `ok` וגרסה — לא מצב הבדיקות
        (‏`/health` נשאר admin בלבד). ‏#1159: ‏`version: null` תמיד בא עם
        ‏`version_error` — "לא נקרא" אינו "אין גרסה" (עיקרון 5). **רק
        הקטגוריה** (‏`update.VERSION_ERRORS`): ה-endpoint ללא הזדהות, והסיבה
        המלאה — נתיבים ו-stderr — ביומן וב-`GET /update` (admin)."""
        if version is None:
            return {"ok": True, "version": None, "version_error": "not-wired"}
        ver = version()
        return {"ok": True, "version": ver.version, "version_error": ver.error}

    @router.get("/health")
    def health(response: Response, user=Depends(admin_only)):
        checks = collect(ctx, hooks, server_base)
        # אחרי המדידה, לא לפניה: "נבדק" הוא הרגע שבו התוצאות נכונות.
        response.headers[CHECKED_AT_HEADER] = now_iso()
        return checks

    @router.get("/ports")
    def list_ports(user=Depends(current_user)):
        """‏#822: מסך הפורטים. מחובר בלבד — בניגוד ל-/health, admin
        אינו נדרש: אלה פורטי תשתית ולא רשימת דלתות פתוחות למנהל בלבד."""
        del user
        return ports_snapshot(ctx, hooks, server_base)

    # ‏#996: המתג לכל פורט — אותם hooks (ss/ss_tcp/port_listeners) כמו החיווי.
    router.include_router(ports.create_ports_router(
        ctx, lambda: ports_snapshot(ctx, hooks, server_base),
        hooks.get("port_listeners"), dnsmasq_apply))
    return router
