"""מיקומי אחסון לספריית האימג'ים — תיקייה / NFS / SMB / iSCSI (#1066 שלב א').

המיקום `local` (`loc_local`) הוא `--images`: נוצר בעלייה, לא ניתן להסרה.
שאר המיקומים הם mount שהמפעיל חיבר. הספרייה מאחדת את כולם; אימג' על
מיקום לא נגיש נשאר ברשימה עם `available: false` (לא "חסר").

כל hook מחזיר שלושה מצבים — `ok` / `failed(reason)` / `unchecked(reason)`
— ולא bool. כלי המערכת רצים רק דרך hooks שמוזרקים ל-`create_app`; הבדיקות
מזייפות אותם ולעולם לא נוגעות במכונה.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from server.core.atomicfile import write_atomic
from server.db import _settle, _write_lock, journal, now_iso, writing


def _tx(conn, sql: str, params: tuple) -> None:
    """כתיבה אחת תחת מנעול הכתיבה — בלי ``commit`` חשוף (#54/#525)."""
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(sql, params)

log = logging.getLogger("imagesrv.storage_locations")

OK, FAILED, UNCHECKED = "ok", "failed", "unchecked"
LOCAL_ID = "loc_local"
LOCAL_NAME = "מקומי"
TYPES = ("local", "nfs", "smb", "iscsi")
STATES = ("connected", "unreachable", "disconnected", "unchecked")
SECRET_KEYS = ("secret", "chap_secret")
TYPE_LABELS = {"local": "תיקייה", "nfs": "NFS", "smb": "SMB", "iscsi": "iSCSI"}
STATE_LABELS = {
    "connected": "מחובר",
    "unreachable": "לא נגיש",
    "disconnected": "מנותק",
    "unchecked": "לא נבדק",
}
FSTAB_BEGIN = "# BEGIN imagesrv-storage"
FSTAB_END = "# END imagesrv-storage"
FSTAB_PATH = Path("/etc/fstab")
#: ‏#1123: קובצי credentials של SMB (פורמט mount.cifs / `smbclient -A`),
#: ‏0600, אחד למיקום. ‏`-/etc/imagesrv` כבר ב-ReadWritePaths של היחידה.
CREDS_DIR = Path("/etc/imagesrv/creds")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
CHECK_INTERVAL = 60.0

# ‏#1123 (3): מה שמגיע מהקונסולה נכנס ל-argv של mount/showmount/iscsiadm
# ול-opts של fstab. subprocess בלי shell — אבל רווח/פסיק בתוך opts הם
# הזרקת אפשרות, ורווח ב-fstab הוא שדה חדש. לכן whitelist, לא blacklist.
_HOST_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_SHARE_RE = re.compile(r"^[A-Za-z0-9_.$-]+$")
_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
# ‏RFC 3720 §3.2.6.3: iqn.YYYY-MM.reverse.domain[:id] · eui.<16 hex> ·
# ‏naa.<16|32 hex> (RFC 3980). ה-id אחרי הנקודתיים — בלי רווח/פסיק.
_IQN_RE = re.compile(
    r"^(iqn\.[0-9]{4}-[0-9]{2}\.[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:[A-Za-z0-9._:-]+)?"
    r"|eui\.[0-9A-Fa-f]{16}|naa\.([0-9A-Fa-f]{16}|[0-9A-Fa-f]{32}))$"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def valid_host(value: str) -> bool:
    """‏IP (v4/v6) או hostname לפי RFC 1123. ריק = לא תקין."""
    host = (value or "").strip()
    if not host or host != value:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if len(host) > 253 or host.endswith("."):
        return False
    return all(_HOST_LABEL.fullmatch(label) for label in host.split("."))


def valid_portal(value: str) -> bool:
    """‏`host[:port]`; ‏IPv6 בסוגריים: ‏`[fd00::75]:3260`."""
    portal = value or ""
    host, port = portal, ""
    if portal.startswith("["):
        end = portal.find("]")
        if end < 0:
            return False
        host, rest = portal[1:end], portal[end + 1:]
        if rest and not rest.startswith(":"):
            return False
        port = rest[1:] if rest else ""
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
    elif portal.count(":") == 1:
        host, port = portal.split(":", 1)
    elif ":" in portal:
        # ‏IPv6 חשוף בלי סוגריים — כתובת בלבד, בלי פורט
        return valid_host(portal)
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        return False
    return valid_host(host)


def validate_params(kind: str, params: dict) -> str | None:
    """‏None = תקין; מחרוזת = סיבת ה-400 (בשם השדה, בעברית)."""
    def clean(key: str) -> bool:
        return not _CONTROL_CHARS.search(str(params.get(key) or ""))

    if kind == "nfs":
        if not valid_host(params.get("server") or ""):
            return "כתובת שרת לא תקינה — IP או שם מארח (RFC 1123)"
        export = params.get("export") or ""
        if (not export.startswith("/") or "," in export
                or re.search(r"\s", export) or not clean("export")):
            return "נתיב ייצוא לא תקין — מתחיל ב-/ ובלי רווח או פסיק"
        version = params.get("version")
        if version not in (None, "") and not _VERSION_RE.fullmatch(str(version)):
            return "גרסת NFS לא תקינה — למשל 4.1 או 3"
        return None
    if kind == "smb":
        if not valid_host(params.get("server") or ""):
            return "כתובת שרת לא תקינה — IP או שם מארח (RFC 1123)"
        if not _SHARE_RE.fullmatch(params.get("share") or ""):
            return "שם שיתוף לא תקין — אותיות, ספרות ו-_ . $ - בלבד"
        if not clean("username") or "=" in str(params.get("username") or ""):
            return "שם משתמש לא תקין — בלי תווי בקרה או ="
        if not clean("domain") or "=" in str(params.get("domain") or ""):
            return "דומיין לא תקין — בלי תווי בקרה או ="
        if not clean("secret"):
            return "סיסמה לא תקינה — בלי תווי בקרה"
        return None
    if kind == "iscsi":
        if not valid_portal(params.get("portal") or ""):
            return "פורטל לא תקין — IP[:port] או שם מארח[:port]"
        iqn = params.get("iqn") or ""
        if iqn and not _IQN_RE.fullmatch(iqn):
            return "IQN לא תקין — iqn.YYYY-MM.domain[:id] / eui. / naa. (RFC 3720)"
        if not clean("chap_user") or not clean("chap_secret"):
            return "פרטי CHAP לא תקינים — בלי תווי בקרה"
        return None
    return None


@dataclass(frozen=True)
class HookResult:
    """תוצאת hook — שלושה מצבים, לא bool (עיקרון 5)."""

    status: str
    reason: str = ""
    payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# הרצת פקודה — שלושה מצבים, לא check=True
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 30, input_text: str | None = None) -> HookResult:
    """מריץ פקודה. כלי חסר = unchecked; כשל = failed; הצלחה = ok + stdout."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            input=input_text,
        )
    except FileNotFoundError:
        return HookResult(UNCHECKED, f"{cmd[0]} לא נמצא במערכת")
    except subprocess.TimeoutExpired:
        return HookResult(FAILED, f"{cmd[0]} לא ענה תוך {timeout} שניות")
    except OSError as exc:
        return HookResult(UNCHECKED, f"לא הצלחנו להריץ {cmd[0]}: {exc}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return HookResult(FAILED, err, {"stdout": proc.stdout, "stderr": proc.stderr,
                                        "returncode": proc.returncode})
    return HookResult(OK, payload={"stdout": proc.stdout or "", "stderr": proc.stderr or ""})


def nfs_scan(server: str) -> HookResult:
    """`showmount -e` — רשימת ייצואים, או failed/unchecked."""
    if not server:
        return HookResult(FAILED, "חסרה כתובת שרת")
    result = _run(["showmount", "-e", server], timeout=20)
    if result.status != OK:
        return result
    exports = []
    for line in result.payload["stdout"].splitlines():
        line = line.strip()
        if not line or line.lower().startswith("export list"):
            continue
        parts = line.split(None, 1)
        exports.append({
            "export": parts[0],
            "clients": parts[1] if len(parts) > 1 else "",
        })
    return HookResult(OK, payload={"items": exports})


def smb_scan(server: str, creds: dict | None = None) -> HookResult:
    """`smbclient -L` — רשימת שיתופים."""
    if not server:
        return HookResult(FAILED, "חסרה כתובת שרת")
    creds = smb_creds(creds or {})
    auth_file = ""
    if creds.get("username"):
        # ‏#1123: ‏`-A <קובץ>` ולא `-U user%password` — הסיסמה לא ב-argv/ps.
        auth_file = creds_path("scan-" + secrets.token_hex(3))
        written = creds_write(auth_file, creds)
        if written.status != OK:
            return written
        cmd = ["smbclient", "-L", server, "-A", auth_file]
    else:
        cmd = ["smbclient", "-L", server, "-N"]
    try:
        result = _run(cmd, timeout=20)
    finally:
        if auth_file:
            creds_remove(auth_file)
    if result.status != OK:
        return result
    shares = []
    in_shares = False
    for line in result.payload["stdout"].splitlines():
        stripped = line.strip()
        if "Sharename" in stripped and "Type" in stripped:
            in_shares = True
            continue
        if in_shares:
            if not stripped or stripped.startswith("-"):
                if shares:
                    break
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] in ("Disk", "IPC", "Printer"):
                if parts[1] == "Disk":
                    shares.append({"share": parts[0], "type": parts[1]})
    return HookResult(OK, payload={"items": shares})


def iscsi_discover(portal: str, chap: dict | None = None) -> HookResult:
    """`iscsiadm -m discovery -t st -p` — SendTargets."""
    if not portal:
        return HookResult(FAILED, "חסר פורטל")
    cmd = ["iscsiadm", "-m", "discovery", "-t", "st", "-p", portal]
    result = _run(cmd, timeout=20)
    if result.status != OK:
        return result
    targets = []
    for line in result.payload["stdout"].splitlines():
        line = line.strip()
        if not line:
            continue
        # "10.44.3.75:3260,1 iqn.2005-10.org.freenas.ctl:images"
        parts = line.split()
        if len(parts) >= 2 and parts[-1].startswith("iqn."):
            targets.append({"portal": parts[0].split(",")[0], "iqn": parts[-1]})
        elif line.startswith("iqn."):
            targets.append({"portal": portal, "iqn": line.split()[0]})
    return HookResult(OK, payload={"items": targets})


def iscsi_login(iqn: str, portal: str, chap: dict | None = None,
                auto: bool = True) -> HookResult:
    """התחברות ליעד. `auto` = node.startup=automatic."""
    if not iqn or not portal:
        return HookResult(FAILED, "חסר IQN או פורטל")
    if chap and chap.get("user"):
        _run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal,
              "-o", "update", "-n", "node.session.auth.authmethod", "-v", "CHAP"])
        _run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal,
              "-o", "update", "-n", "node.session.auth.username", "-v", chap["user"]])
        if chap.get("secret"):
            _run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal,
                  "-o", "update", "-n", "node.session.auth.password", "-v", chap["secret"]])
    startup = "automatic" if auto else "manual"
    _run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal,
          "-o", "update", "-n", "node.startup", "-v", startup])
    result = _run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal, "-l"], timeout=30)
    if result.status != OK:
        return result
    by_path = _iscsi_by_path(portal, iqn)
    return HookResult(OK, payload={"device_by_path": by_path or "", "iqn": iqn,
                                   "portal": portal})


def iscsi_logout(iqn: str, portal: str, chap: dict | None = None,
                 auto: bool = False) -> HookResult:
    del chap, auto
    if not iqn:
        return HookResult(FAILED, "חסר IQN")
    cmd = ["iscsiadm", "-m", "node", "-T", iqn, "-u"]
    if portal:
        cmd.extend(["-p", portal])
    return _run(cmd, timeout=20)


def _iscsi_by_path(portal: str, iqn: str) -> str:
    """נתיב by-path אחרי login, או ריק אם לא נמצא — לא ניחוש."""
    host = portal.split(":")[0]
    root = Path("/dev/disk/by-path")
    if not root.is_dir():
        return ""
    try:
        entries = list(root.iterdir())
    except OSError:
        return ""
    needle = f"ip-{host}"
    for entry in entries:
        name = entry.name
        if needle in name and iqn in name:
            try:
                return str(entry.resolve())
            except OSError:
                return str(entry)
    return ""


def disk_probe(device: str) -> HookResult:
    """blkid + lsblk. שלושה מצבים: FS / ריק (ראיה חיובית) / unknown."""
    if not device:
        return HookResult(UNCHECKED, "אין התקן לבדוק")
    blkid = _run(["blkid", "-o", "export", device], timeout=30)
    lsblk = _run(["lsblk", "-b", "-dn", "-o", "SIZE,NAME", device], timeout=10)
    size = None
    if lsblk.status == OK:
        parts = (lsblk.payload.get("stdout") or "").split()
        if parts and parts[0].isdigit():
            size = int(parts[0])
    if blkid.status == UNCHECKED:
        return HookResult(UNCHECKED, blkid.reason, {"device": device, "fs_size": size})
    if blkid.status == FAILED:
        # blkid יוצא 2 כשאין חתימה — זו ראיה לריק, לא כשל קריאה.
        rc = (blkid.payload or {}).get("returncode")
        err = blkid.reason.lower()
        io_error = "input/output" in err or "i/o error" in err or "i/o error" in err
        if io_error or (rc not in (2, None) and "not found" not in err
                        and rc not in (0, 2)):
            if rc == 2 or "no token" in err or "no uuid" in err:
                pass
            else:
                return HookResult(UNCHECKED, blkid.reason,
                                  {"device": device, "disk_status": "unknown",
                                   "fs_size": size})
        empty = _zeros_evidence(device)
        if empty is True:
            return HookResult(OK, payload={"device": device, "disk_status": "empty",
                                           "fs_type": None, "fs_label": None,
                                           "fs_size": size})
        if empty is False:
            return HookResult(UNCHECKED, "blkid לא זיהה חתימה וגם לא הצלחנו לאשר ריק",
                              {"device": device, "disk_status": "unknown",
                               "fs_size": size})
        return HookResult(UNCHECKED, empty or "לא הצלחנו לקרוא את הדיסק",
                          {"device": device, "disk_status": "unknown", "fs_size": size})
    fields = {}
    for line in (blkid.payload.get("stdout") or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    fs_type = fields.get("TYPE")
    if not fs_type:
        empty = _zeros_evidence(device)
        if empty is True:
            return HookResult(OK, payload={"device": device, "disk_status": "empty",
                                           "fs_type": None, "fs_label": None,
                                           "fs_size": size})
        return HookResult(UNCHECKED, "blkid לא החזיר TYPE",
                          {"device": device, "disk_status": "unknown", "fs_size": size})
    return HookResult(OK, payload={
        "device": device, "disk_status": "fs",
        "fs_type": fs_type, "fs_label": fields.get("LABEL"),
        "fs_uuid": fields.get("UUID"), "fs_size": size,
    })


def _zeros_evidence(device: str, *, strict_tail: bool = False) -> bool | str:
    """True = 1MB בהתחלה ובסוף אפסים. False = יש נתונים. str = לא הצלחנו.

    ‏`strict_tail` (#1988): זנב שלא נקרא אינו "אפסים" — שם יושבת טבלת ה-GPT
    המשנית, ו"הוסף כונן" מחליט לפי זה אם להציע את הכונן למחיקה."""
    try:
        with open(device, "rb") as fh:
            head = fh.read(1 << 20)
            try:
                fh.seek(-1 << 20, os.SEEK_END)
                tail = fh.read(1 << 20)
            except OSError as exc:
                if strict_tail:
                    return f"סוף הכונן לא נקרא: {exc}"
                tail = b"\x00" * (1 << 20)
    except OSError as exc:
        return str(exc)
    if strict_tail and not tail:
        return "קריאה ריקה מסוף הכונן — לא ראיה לריק"
    if not head:
        return "קריאה ריקה — לא ראיה לריק"
    if head == b"\x00" * len(head) and tail == b"\x00" * len(tail):
        return True
    return False


def make_fs(device: str, label: str = "") -> HookResult:
    """יצירת ext4 — הפעולה ההרסנית היחידה."""
    if not device:
        return HookResult(FAILED, "אין התקן לפירמוט")
    cmd = ["mkfs.ext4", "-F"]
    if label:
        cmd.extend(["-L", label[:16]])
    cmd.append(device)
    return _run(cmd, timeout=120)


# ---------------------------------------------------------------------------
# ‏#1988: "הוסף כונן" — כונן ריק בשרת. כאן רק כלי המערכת (hooks); הזרימה
# עצמה — אימות מול הרשימה, עיגון, קריאה חוזרת, השורה ו-fstab — היא
# `storage_disks.add_disk`, המשותפת לקונסולה ולמסך השרת.
# ---------------------------------------------------------------------------

#: העץ המלא (ילדים = מחיצות/מערכים), בבתים.
LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,RO,RM,TRAN,FSTYPE,PTTYPE"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
#: תקרת ההמתנה לצומת המחיצה אחרי sgdisk (udev). בשניות.
PARTITION_WAIT_S = 10


def _flag(value: object) -> bool:
    return value in (True, 1, "1", "true")


def classify_disks(tree: dict) -> tuple[list[dict], list[dict]]:
    """‏`lsblk -J` → (מועמדים, מוסתרים עם סיבה). לא קורא את הכונן עצמו.

    מועמד = דיסק שלם, כתיב, לא נשלף/USB, בלי ילדים, בלי טבלת מחיצות ובלי
    חתימה ש-lsblk מכיר. זה תנאי הכרחי בלבד — "ריק" נקבע ב-`probe_blank`."""
    candidates: list[dict] = []
    hidden: list[dict] = []
    for node in tree.get("blockdevices") or []:
        if not isinstance(node, dict) or node.get("type") != "disk":
            continue
        name = str(node.get("name") or "")
        if not name or name.startswith(("zram", "ram", "loop")):
            continue
        try:
            size = int(node.get("size"))
        except (TypeError, ValueError):
            size = None
        info = {
            "device": str(node.get("path") or f"/dev/{name}"),
            "name": name,
            "size_bytes": size,
            "model": str(node.get("model") or "").strip(),
            "serial": str(node.get("serial") or "").strip(),
            "bus": str(node.get("tran") or "").strip(),
        }
        children = node.get("children") or []
        if _flag(node.get("ro")):
            reason = "לקריאה בלבד"
        elif _flag(node.get("rm")) or info["bus"] == "usb":
            reason = "כונן נשלף או USB"
        elif children:
            reason = f"יש עליו {len(children)} מחיצות או מערכים"
        elif node.get("pttype"):
            reason = f"יש עליו טבלת מחיצות ({node.get('pttype')})"
        elif node.get("fstype"):
            reason = f"יש עליו חתימה ({node.get('fstype')})"
        elif not size:
            reason = "הגודל לא נקרא"
        else:
            reason = ""
        if reason:
            hidden.append({**info, "reason": reason})
        else:
            candidates.append(info)
    return candidates, hidden


def _export_fields(text: str) -> dict:
    fields = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def probe_blank(device: str, run=None, zeros=None) -> tuple[bool, str]:
    """ראיה חיובית לריק (#1988): ‏`blkid -p` לא מצא שום חתימה — מערכת קבצים,
    טבלת מחיצות, RAID — (יציאה 2), **וגם** 1MB בראש הכונן ובסופו אפסים.

    כל דבר אחר הוא "לא ריק" עם הסיבה — כולל כשל בקריאה (עיקרון 5): כונן
    שלא הצלחנו לקרוא לעולם אינו מוצע למחיקה."""
    run = run or _run
    zeros = zeros or (lambda dev: _zeros_evidence(dev, strict_tail=True))
    probed = run(["blkid", "-p", "-o", "export", device], timeout=30)
    if probed.status == OK:
        found = _export_fields((probed.payload or {}).get("stdout", ""))
        what = found.get("TYPE") or found.get("PTTYPE") or "חתימה"
        return False, f"blkid מצא עליו {what}"
    if probed.status == UNCHECKED:
        return False, f"לא נבדק: {probed.reason}"
    rc = (probed.payload or {}).get("returncode")
    if rc != 2:
        return False, f"blkid נכשל (יציאה {rc}): {probed.reason}"
    evidence = zeros(device)
    if evidence is True:
        return True, ""
    if evidence is False:
        return False, "יש נתונים בראש הכונן או בסופו"
    return False, f"קריאת הכונן נכשלה: {evidence}"


def list_blank_disks(run=None, probe=None) -> HookResult:
    """hook ‏`blank_disks`: ‏`items` = כוננים ריקים לפי ראיה, ‏`hidden` = כל
    השאר עם הסיבה (המפעיל רואה למה כונן חסר — לא רשימה שקטה)."""
    run = run or _run
    probe = probe or probe_blank
    listed = run(["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS], timeout=20)
    if listed.status != OK:
        return listed
    try:
        tree = json.loads((listed.payload or {}).get("stdout") or "")
    except ValueError:
        return HookResult(FAILED, "פלט lsblk אינו JSON")
    if not isinstance(tree, dict):
        return HookResult(FAILED, "פלט lsblk אינו עץ התקנים")
    candidates, hidden = classify_disks(tree)
    items = []
    for disk in candidates:
        blank, why = probe(disk["device"])
        if blank:
            items.append(disk)
        else:
            hidden.append({**disk, "reason": why})
    return HookResult(OK, payload={"items": items, "hidden": hidden})


def _partitions_of(run, device: str) -> tuple[list[str] | None, str]:
    """המחיצות הישירות של `device`, או (None, הסיבה שלא נקראו).

    ‏#2102: בלי עמודת NAME, ‏lsblk 2.41 (דביאן 13) מחזיר רשימה **שטוחה**;
    עם NAME — עץ עם `children`. לא מנחשים צורה: מבקשים PKNAME במפורש,
    משטחים את שתי הצורות, ובוחרים `type=part` שההורה שלה הוא הכונן.
    פלט שאי אפשר לפרש הוא סיבה בשם, לא רשימה ריקה (עיקרון 5)."""
    listed = run(["lsblk", "-J", "-o", "PATH,TYPE,PKNAME", device], timeout=10)
    if listed.status != OK:
        return None, f"lsblk נכשל: {listed.reason}"
    try:
        tree = json.loads((listed.payload or {}).get("stdout") or "")
    except ValueError:
        return None, "פלט lsblk אינו JSON"
    pending = tree.get("blockdevices") if isinstance(tree, dict) else None
    if not isinstance(pending, list):
        return None, "בפלט lsblk אין רשימת blockdevices"
    nodes = []
    while pending:
        node = pending.pop(0)
        if not isinstance(node, dict) or "pkname" not in node:
            return None, "בפלט lsblk אין עמודת PKNAME"
        children = node.get("children") or []
        if not isinstance(children, list):
            return None, "בפלט lsblk יש children שאינו רשימה"
        nodes.append(node)
        pending.extend(children)
    if not any(node.get("path") == device for node in nodes):
        return None, f"lsblk לא החזיר את {device}"
    name = device.rsplit("/", 1)[-1]
    found = []
    for node in nodes:
        if node.get("type") == "part" and node.get("pkname") == name:
            if not node.get("path"):
                return None, "בפלט lsblk יש מחיצה בלי PATH"
            found.append(str(node["path"]))
    return found, ""


def prepare_disk(device: str, label: str, run=None, sleep=None) -> HookResult:
    """hook ‏`prepare_disk` (#1988): ‏GPT עם מחיצה אחת על כל הכונן, ‏ext4
    עליה, ו-UUID שנקרא בחזרה. הפעולה ההרסנית — רק אחרי `probe_blank`.

    הצלחה = ‏`blkid -p` על המחיצה מחזיר ‏TYPE=ext4 ו-UUID תקין (ראיה), לא
    היעדר שגיאה מ-mkfs."""
    run = run or _run
    sleep = sleep or time.sleep
    if not device:
        return HookResult(FAILED, "אין כונן")
    made = run(["sgdisk", "--new=1:1MiB:0", "--typecode=1:8300",
                f"--change-name=1:{label[:36]}", device], timeout=60)
    if made.status != OK:
        return HookResult(made.status, f"יצירת המחיצה נכשלה: {made.reason}")
    partition = ""
    for _ in range(PARTITION_WAIT_S):
        run(["udevadm", "settle", "--timeout=5"], timeout=10)
        parts, why = _partitions_of(run, device)
        if parts is None:
            return HookResult(FAILED, f"לא הצלחנו לקרוא את המחיצות שעל הכונן: {why}")
        if len(parts) > 1:
            return HookResult(FAILED, f"על הכונן {len(parts)} מחיצות אחרי החלוקה — עצרנו")
        if parts and Path(parts[0]).exists():
            partition = parts[0]
            break
        sleep(1)
    if not partition:
        return HookResult(FAILED, f"המחיצה לא הופיעה תוך {PARTITION_WAIT_S} שניות")
    fs = run(["mkfs.ext4", "-F", "-L", label[:16], partition], timeout=900)
    if fs.status != OK:
        return HookResult(fs.status, f"יצירת מערכת הקבצים נכשלה: {fs.reason}",
                          {"partition": partition})
    probed = run(["blkid", "-p", "-o", "export", partition], timeout=30)
    fields = _export_fields((probed.payload or {}).get("stdout", "")) if probed.status == OK else {}
    if fields.get("TYPE") != "ext4" or not UUID_RE.fullmatch(fields.get("UUID", "")):
        return HookResult(FAILED, "אחרי היצירה blkid לא קרא ext4 עם UUID",
                          {"partition": partition})
    return HookResult(OK, payload={"partition": partition, "uuid": fields["UUID"],
                                   "fs_type": "ext4"})


# ‏#2122: ‏imagesrv-server (וגם imagesrv-dcui) רץ עם `ProtectSystem=strict`, כלומר
# במרחב עיגון **משלו**. ‏`mount` שרץ משם נשאר שם: ‏PID 1 לא רואה אותו, ‏restart של
# השירות מוחק אותו, ו-`storage/<שם>` חוזר להיות תיקייה על דיסק המערכת — שאימג'ים
# נכתבים אליה בשקט. לכן mount/umount רצים במרחב העיגון של PID 1 (המארח), ומשם
# מתפשטים לשירות (slave). הראיה להצלחה היא שני קובצי mountinfo — לא קוד היציאה.
HOST_MOUNT_NS = "/proc/1/ns/mnt"
HOST_MOUNTINFO = "/proc/1/mountinfo"
OWN_MOUNTINFO = "/proc/self/mountinfo"
_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")


def _on_host(cmd: list[str]) -> list[str]:
    return ["nsenter", f"--mount={HOST_MOUNT_NS}", "--", *cmd]


def _mount_points(path: str) -> set[str] | None:
    """נקודות העיגון בקובץ mountinfo (שדה 5, עם `\\040` וחבריו). ‏None = לא נקרא."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    points = set()
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) > 4:
            points.add(_MOUNTINFO_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), fields[4]))
    return points


def mount_presence(target: str) -> HookResult:
    """‏ok = מעוגן גם אצל PID 1 וגם אצל השירות; failed = חסר אצל אחד מהם לפחות
    (‏`payload["missing"]`); unchecked = קובץ mountinfo לא נקרא — אינו "לא מעוגן"."""
    want = posixpath.normpath(str(target))
    missing = []
    for label, path in (("PID 1", HOST_MOUNTINFO), ("השירות", OWN_MOUNTINFO)):
        points = _mount_points(path)
        if points is None:
            return HookResult(UNCHECKED, f"לא הצלחנו לקרוא את {path}")
        if want not in points:
            missing.append(label)
    if missing:
        return HookResult(FAILED, f"{want} אינו מעוגן אצל {' ואצל '.join(missing)}",
                          {"missing": missing})
    return HookResult(OK)


def is_mounted(target: str) -> bool:
    """ה-`is_mount` של ברירת המחדל: מעוגן אצל PID 1 **וגם** אצל השירות."""
    seen = mount_presence(target)
    if seen.status == UNCHECKED:
        log.warning("mount check for %s could not run: %s", target, seen.reason)
    return seen.status == OK


def mount(fstype: str, source: str, target: str, opts: str = "") -> HookResult:
    Path(target).mkdir(parents=True, exist_ok=True)
    cmd = ["mount"]
    if fstype:
        cmd.extend(["-t", fstype])
    if opts:
        cmd.extend(["-o", opts])
    cmd.extend([source, target])
    result = _run(_on_host(cmd), timeout=30)
    if result.status != OK:
        return result
    seen = mount_presence(target)
    if seen.status != OK:
        _run(_on_host(["umount", target]), timeout=20)
        return HookResult(seen.status, f"mount החזיר 0, אבל {seen.reason}", seen.payload)
    return result


def umount(target: str) -> HookResult:
    if not target:
        return HookResult(FAILED, "חסרה נקודת עיגון")
    result = _run(_on_host(["umount", target]), timeout=20)
    if result.status != OK:
        return result
    seen = mount_presence(target)
    if seen.status == UNCHECKED:
        return seen
    if len((seen.payload or {}).get("missing") or []) < 2:
        return HookResult(FAILED, f"umount החזיר 0, אבל {target} עדיין מעוגן")
    return result


def daemon_reload() -> HookResult:
    """‏#2122: ‏fstab שהשתנה בלי daemon-reload משאיר ל-PID 1 יחידת mount ישנה; עיגון
    באותה נקודה עם כונן אחר נקשר אליה — ו-systemd מנתק אותו כשההתקן הישן איננו."""
    return _run(["systemctl", "daemon-reload"], timeout=60)


def fstab_write(entries: list[dict]) -> HookResult:
    """כותב את בלוק ImageSrv ב-fstab עם `_netdev,nofail`.

    ‏#1123 (2): אטומי — קובץ זמני ליד fstab ו-`os.replace`. קריסה באמצע
    כתיבה ישירה = fstab חצי-כתוב = שרת שלא עולה.
    """
    path = FSTAB_PATH
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        return HookResult(UNCHECKED, f"לא הצלחנו לקרוא את fstab: {exc}")
    lines = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == FSTAB_BEGIN:
            skipping = True
            continue
        if stripped == FSTAB_END:
            skipping = False
            continue
        if not skipping:
            lines.append(line)
    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")
    block = [FSTAB_BEGIN + "\n"]
    for entry in entries:
        opts = entry.get("opts") or "defaults"
        # ‏#1988: כונן מקומי אינו התקן רשת — `_netdev` היה דוחה אותו לאחרי הרשת.
        if "_netdev" not in opts and not entry.get("local"):
            opts = opts + ",_netdev"
        if "nofail" not in opts:
            opts = opts + ",nofail"
        block.append(
            f"{entry['source']} {entry['target']} {entry.get('fstype', 'auto')} "
            f"{opts} 0 0\n"
        )
    block.append(FSTAB_END + "\n")
    new_text = "".join(lines) + ("" if not entries else "".join(block))
    try:
        write_atomic(path, new_text)
    except OSError as exc:
        return HookResult(FAILED, f"לא הצלחנו לכתוב fstab: {exc}")
    reloaded = daemon_reload()
    if reloaded.status != OK:
        return HookResult(reloaded.status,
                          f"fstab נכתב, אבל systemctl daemon-reload נכשל: {reloaded.reason}")
    return HookResult(OK, payload={"entries": len(entries)})


def creds_path(loc_id: str) -> str:
    """‏#1123: איפה קובץ ה-credentials של מיקום SMB חי — נגזר מה-id, לא נשמר."""
    return (CREDS_DIR / loc_id).as_posix()


def smb_creds(params: dict) -> dict:
    """מה שנכנס לקובץ — בשמות של mount.cifs (`password`, לא `secret`)."""
    creds = {}
    if params.get("username"):
        creds["username"] = str(params["username"])
    if params.get("secret"):
        creds["password"] = str(params["secret"])
    if params.get("domain"):
        creds["domain"] = str(params["domain"])
    return creds


def creds_write(path: str, creds: dict) -> HookResult:
    """קובץ credentials ‏0600 (פורמט mount.cifs / smbclient -A), אטומי."""
    target = Path(path)
    text = "".join(f"{key}={creds[key]}\n" for key in ("username", "password", "domain")
                   if creds.get(key))
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_atomic(target, text, mode=0o600)
    except OSError as exc:
        return HookResult(FAILED, f"לא הצלחנו לכתוב את קובץ ה-credentials: {exc}")
    return HookResult(OK, payload={"path": str(target)})


def creds_remove(path: str) -> HookResult:
    """מחיקת הקובץ. "כבר איננו" הוא המצב הרצוי — לא כשל."""
    target = Path(path)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        return HookResult(FAILED, f"לא הצלחנו למחוק את קובץ ה-credentials: {exc}")
    if target.exists():
        return HookResult(FAILED, f"הקובץ עדיין קיים אחרי מחיקה: {path}")
    return HookResult(OK)


def df(target: str) -> HookResult:
    """מקום פנוי — shutil, לא ניחוש לפי היעדר שגיאה."""
    if not target:
        return HookResult(FAILED, "חסרה נקודת עיגון")
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return HookResult(FAILED, str(exc))
    return HookResult(OK, payload={"free_bytes": usage.free, "total_bytes": usage.total})


def default_hooks() -> dict:
    return {
        "nfs_scan": nfs_scan,
        "smb_scan": smb_scan,
        "iscsi_discover": iscsi_discover,
        "iscsi_login": iscsi_login,
        "iscsi_logout": iscsi_logout,
        "disk_probe": disk_probe,
        "make_fs": make_fs,
        "mount": mount,
        "umount": umount,
        "fstab_write": fstab_write,
        "creds_write": creds_write,
        "creds_remove": creds_remove,
        "df": df,
        "interfaces": lambda: [],
        "blank_disks": list_blank_disks,     # ‏#1988
        "prepare_disk": prepare_disk,        # ‏#1988 — ההרסני
        "is_mount": is_mounted,              # ‏#1988/#2122 — עיגון כראיה, אצל PID 1 ואצל השירות
    }


# ---------------------------------------------------------------------------
# מודל
# ---------------------------------------------------------------------------

def ensure_local(conn, images_root: str | Path, *, user: str = "") -> None:
    """המיקום המובנה — `--images`. נוצר בעלייה, id קבוע, לא ניתן להסרה."""
    root = str(Path(images_root))
    now = now_iso()
    row = conn.execute(
        "SELECT id, mount_point FROM storage_locations WHERE id = ?", (LOCAL_ID,)
    ).fetchone()
    if row is None:
        _tx(conn,
            "INSERT INTO storage_locations (id, name, type, params_json, mount_point,"
            " state, state_since, state_detail, created_by, created_at,"
            " last_images_json, last_df_json)"
            " VALUES (?, ?, 'local', '{}', ?, 'connected', ?, '', ?, ?, '[]', NULL)",
            (LOCAL_ID, LOCAL_NAME, root, now, user, now))
        return
    if row["mount_point"] != root:
        _tx(conn, "UPDATE storage_locations SET mount_point = ? WHERE id = ?",
            (root, LOCAL_ID))


def get(conn, loc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM storage_locations WHERE id = ?", (loc_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_rows(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM storage_locations ORDER BY CASE id WHEN ? THEN 0 ELSE 1 END,"
        " created_at",
        (LOCAL_ID,),
    ).fetchall()
    return [dict(r) for r in rows]


def params_of(row: dict) -> dict:
    try:
        data = json.loads(row.get("params_json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def public_params(params: dict) -> dict:
    """סודות לא נחשפים ב-GET."""
    return {k: v for k, v in params.items() if k not in SECRET_KEYS}


def set_state(conn, loc_id: str, state: str, detail: str = "", *,
              force_since: bool = False) -> None:
    row = get(conn, loc_id)
    if row is None:
        return
    since = now_iso() if force_since or row["state"] != state else row["state_since"]
    _tx(conn,
        "UPDATE storage_locations SET state = ?, state_since = ?, state_detail = ?"
        " WHERE id = ?",
        (state, since, detail, loc_id))


def save_params(conn, loc_id: str, params: dict) -> None:
    _tx(conn, "UPDATE storage_locations SET params_json = ? WHERE id = ?",
        (json.dumps(params, ensure_ascii=False), loc_id))


def save_df(conn, loc_id: str, free_bytes: int | None, total_bytes: int | None) -> None:
    payload = {"free_bytes": free_bytes, "total_bytes": total_bytes,
               "checked_at": now_iso()}
    _tx(conn, "UPDATE storage_locations SET last_df_json = ? WHERE id = ?",
        (json.dumps(payload), loc_id))


def save_catalog(conn, loc_id: str, images: list[dict]) -> None:
    _tx(conn, "UPDATE storage_locations SET last_images_json = ? WHERE id = ?",
        (json.dumps(images, ensure_ascii=False), loc_id))


def last_df(row: dict) -> dict:
    try:
        data = json.loads(row.get("last_df_json") or "null")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def last_images(row: dict) -> list[dict]:
    try:
        data = json.loads(row.get("last_images_json") or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def images_dir(row: dict) -> Path:
    """איפה האימג'ים חיים: `--images` עצמו, ובשאר — `images/` תחת ה-mount.

    כונן שנוסף ב"הוסף כונן" (#1988, ‏`uuid` ב-params) הוא mount כמו כל השאר:
    ‏`images/` תחתיו, כך ש-`lost+found` של ext4 אינו נסרק כאימג'."""
    mount = Path(row["mount_point"])
    if row["type"] == "local" and not is_disk(row):
        return mount
    return mount / "images"


def library_roots(conn, images_root: str | Path) -> list[dict]:
    """מה שהספרייה סורקת: מיקום, נתיב, זמינות, ומטמון כשלא זמין."""
    del images_root
    roots = []
    for row in list_rows(conn):
        available = row["state"] == "connected"
        roots.append({
            "id": row["id"],
            "name": row["name"],
            "path": images_dir(row),
            "available": available,
            "state": row["state"],
            "state_since": row["state_since"],
            "state_detail": row["state_detail"],
            "cached": [] if available else last_images(row),
        })
    return roots


def is_disk(row: dict) -> bool:
    """מיקום local שהוא כונן שהשרת הכין ומעגן לפי UUID (#1988), לא תיקייה."""
    return row.get("type") == "local" and bool(params_of(row).get("uuid"))


def new_id() -> str:
    return "loc_" + secrets.token_hex(4)


def mount_point_for(data_dir: Path, name: str, loc_id: str) -> Path:
    # ‏#1956: שם שמתחיל בנקודה מקבל את ה-id — ‏`storage/.db-backup` הוא נקודת העיגון של גיבוי המסד.
    token = name if SAFE_NAME.fullmatch(name) and not name.startswith(".") else loc_id
    return Path(data_dir) / "storage" / token


def target_display(row: dict) -> str:
    params = params_of(row)
    kind = row["type"]
    if kind == "nfs":
        return f"{params.get('server', '')}:{params.get('export', '')}"
    if kind == "smb":
        return f"//{params.get('server', '')}/{params.get('share', '')}"
    if kind == "iscsi":
        return params.get("iqn") or params.get("portal") or row["mount_point"]
    return row["mount_point"]


def subtitle_of(row: dict) -> str:
    if row["id"] == LOCAL_ID:
        return "דיסק השרת · ברירת המחדל לקליטה"
    params = params_of(row)
    if is_disk(row):
        bits = [params.get("model") or "", params.get("serial") or params.get("device") or ""]
        return " · ".join(bit for bit in bits if bit) or f"UUID={params['uuid']}"
    if row["type"] == "iscsi":
        bits = []
        if params.get("fs_type"):
            bits.append(params["fs_type"])
        if params.get("fs_label"):
            bits.append(params["fs_label"])
        return " · ".join(bits) if bits else (params.get("portal") or "")
    if row["type"] == "nfs":
        ver = params.get("version") or "4.1"
        return f"nfs {ver}"
    if row["type"] == "smb":
        user = params.get("username") or ""
        domain = params.get("domain") or ""
        return f"{domain}\\{user}" if domain and user else user
    return params.get("path") or row["mount_point"]


def image_counts(library, loc_id: str) -> tuple[int, int]:
    """(סה\"כ, לא-זמינים) למיקום."""
    total = 0
    unavailable = 0
    for manifest in library.scan().values():
        if manifest.get("_location_id") != loc_id:
            continue
        total += 1
        if not manifest.get("_available", True):
            unavailable += 1
    return total, unavailable


def public_row(row: dict, library) -> dict:
    df_info = last_df(row)
    total, unavailable = image_counts(library, row["id"])
    params = params_of(row)
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "type_label": "כונן" if is_disk(row) else TYPE_LABELS.get(row["type"], row["type"]),
        "target": target_display(row),
        "mount_point": row["mount_point"],
        "state": row["state"],
        "state_label": STATE_LABELS.get(row["state"], row["state"]),
        "state_since": row["state_since"],
        "state_detail": row["state_detail"],
        "free_bytes": df_info.get("free_bytes"),
        "total_bytes": df_info.get("total_bytes"),
        "images": total,
        "unavailable_images": unavailable,
        "subtitle": subtitle_of(row),
        "params": public_params(params),
        "removable": row["id"] != LOCAL_ID,
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }


def summary_of(rows: list[dict], library) -> dict:
    unreachable = sum(1 for r in rows if r["state"] == "unreachable")
    images = library.scan()
    unavailable = sum(1 for m in images.values() if not m.get("_available", True))
    free = 0
    total = 0
    checked = None
    for r in rows:
        info = last_df(r)
        if info.get("free_bytes") is not None:
            free += int(info["free_bytes"])
        if info.get("total_bytes") is not None:
            total += int(info["total_bytes"])
        at = info.get("checked_at")
        if at and (checked is None or at > checked):
            checked = at
    return {
        "locations": len(rows),
        "unreachable": unreachable,
        "images": len(images),
        "unavailable_images": unavailable,
        "free_bytes": free,
        "total_bytes": total,
        "checked_at": checked,
    }


def catalog_entry(manifest: dict) -> dict:
    """מה שנשמר כשמנתקים — מספיק לרשימה ולסירוב סבב, בלי נתיב דיסק."""
    skip = {"_dir"}
    return {k: v for k, v in manifest.items() if k not in skip}


def refresh_catalog(conn, library) -> None:
    """מעדכן last_images_json לכל מיקום מחובר — הדיסק הוא מקור האמת."""
    by_loc: dict[str, list] = {}
    for row in list_rows(conn):
        if row["state"] == "connected":
            by_loc[row["id"]] = []
    for manifest in library.scan().values():
        loc_id = manifest.get("_location_id") or LOCAL_ID
        if loc_id in by_loc and manifest.get("_available", True):
            by_loc[loc_id].append(catalog_entry(manifest))
    for loc_id, images in by_loc.items():
        save_catalog(conn, loc_id, images)


def location_writable(conn, loc_id: str = LOCAL_ID) -> bool:
    row = get(conn, loc_id)
    return row is not None and row["state"] == "connected"


def unavailable_message(manifest: dict | None) -> str | None:
    """None = זמין (או חסר — הקורא מטפל ב-404). מחרוזת = סיבת 409."""
    if manifest is None or manifest.get("_available", True):
        return None
    name = manifest.get("_location_name") or manifest.get("_location_id") or "המיקום"
    since = manifest.get("_state_since") or ""
    if since:
        return f"האימג' לא זמין — {name} לא נגיש מאז {since}"
    return f"האימג' לא זמין — {name} לא נגיש"


# ---------------------------------------------------------------------------
# אזהרת NIC משותף עם כרטיס ההפצה (R10)
# ---------------------------------------------------------------------------

def _host_ip(value: str) -> ipaddress.IPv4Address | None:
    host = (value or "").strip()
    if not host:
        return None
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.split("%")[0]
    if ":" in host and not host.startswith("["):
        # portal "10.44.3.75:3260" — לא IPv6
        left, right = host.rsplit(":", 1)
        if right.isdigit():
            host = left
    try:
        return ipaddress.IPv4Address(host)
    except ValueError:
        return None


def shared_nic_warning(server: str, interfaces: list, server_base: str = "") -> str | None:
    """אזהרה רק בראיה חיובית: השרת/פורטל ב-subnet של כרטיס ההפצה."""
    storage_ip = _host_ip(server)
    if storage_ip is None:
        return None
    deploy_ip = _host_ip(server_base)
    deploy_nets: list[ipaddress.IPv4Network] = []
    for nic in interfaces or []:
        for addr in nic.get("addresses") or []:
            try:
                net = ipaddress.ip_network(addr, strict=False)
            except ValueError:
                continue
            if not isinstance(net, ipaddress.IPv4Network):
                continue
            if deploy_ip is not None and deploy_ip in net:
                deploy_nets.append(net)
    if not deploy_nets and deploy_ip is not None:
        # כרטיס ההפצה הוא הכתובת שב-server_base, גם בלי רשימת ממשקים.
        try:
            deploy_nets.append(ipaddress.ip_network(f"{deploy_ip}/24", strict=False))
        except ValueError:
            return None
    for net in deploy_nets:
        if storage_ip in net:
            return (f"האחסון ({storage_ip}) יושב ב-subnet של כרטיס ההפצה "
                    f"({net}) — מולטיקאסט ואחסון על NIC אחד מאטים זה את זה")
    return None


# ---------------------------------------------------------------------------
# בדיקת חיבור (test) לפי סוג
# ---------------------------------------------------------------------------

#: ‏#1812: תקרת הזמן של בקשת NFS תקועה — כתובה כאן ולא נשענת על ברירת
#: המחדל של הקרנל. ‏`timeo` בעשיריות שנייה; ב-TCP הקרנל מגדיל את ההמתנה
#: ליניארית אחרי כל ניסיון חוזר, ולכן בקשה מוותרת אחרי 60+120+180 ≈ 6 דקות
#: (‏nfs(5) — חישוב, לא מדידה) ומחזירה EIO/ETIMEDOUT. אלה **ערכי ברירת
#: המחדל של הקרנל ל-TCP** — מיקום קיים מתנהג בדיוק כמו קודם; מה שהשתנה
#: הוא שהתקרה כתובה ונבדקת. ‏NAS שמאותחל (2–5 דקות) עובר בלי כשל.
NFS_TIMEO_DECISECONDS = 600
NFS_RETRANS = 2


def _nfs_opts(params: dict) -> str:
    """אפשרויות העיגון של NFS — ‏`soft` נשאר, בהכרעה מנומקת (#1812).

    ‏**הבעיה של `soft`:** אחרי `timeo×retrans` הקרנל מוותר, ו-`write()` שכבר
    החזיר הצלחה (ל-page cache) מאבד את הבייטים שלו. **אבל הוויתור אינו
    שקט למי ששואל:** השגיאה נשמרת על הקובץ ומוחזרת ב-`fsync`/`close` של
    ה-fd שכתב. לכן התיקון הוא בצד הכותב (#1813, ‏`server/core/atomicfile.py`):
    כל נתיב שמכניס אימג' לספרייה עושה fsync לכל קובץ לפני הפרסום, וכשל
    fsync הוא חריגה שהופכת לכשל בשם — לא אימג' "תקין".

    ‏**למה לא `hard`:** בקשה תקועה ב-`hard` לא נגמרת לעולם — התהליכון
    נתקע ב-D-state, ‏`systemctl stop` ממתין עד SIGKILL (‏SVR-27), והמפעיל
    רואה קליטה "רצה" בלי סוף ובלי הודעה. תקרת זמן ברמת האפליקציה לא
    משחררת את התהליכון התקוע ולא את הקובץ שהוא מחזיק. ב-`soft` הקרנל
    עצמו מחזיר שגיאה אחרי `NFS_TIMEO_DECISECONDS`/`NFS_RETRANS`, והקוד
    שלנו מכריע כשל בשם — תקיעה הופכת לכשל גלוי עם תקרה.
    """
    ver = str(params.get("version") or "4.1")
    opts = [f"vers={ver}", "soft", f"timeo={NFS_TIMEO_DECISECONDS}",
            f"retrans={NFS_RETRANS}"]
    if params.get("readonly"):
        opts.append("ro")
    return ",".join(opts)


def _smb_opts(params: dict, creds_file: str = "") -> str:
    """‏#1123: הסיסמה לעולם לא כאן — רק `credentials=<קובץ>` (0600)."""
    opts = ["vers=3.0"]
    if creds_file:
        opts.append(f"credentials={creds_file}")
    if params.get("readonly"):
        opts.append("ro")
    return ",".join(opts)


def _write_probe(target: Path, readonly: bool) -> HookResult:
    if readonly:
        return HookResult(OK, "לקריאה בלבד — בלי כתיבת קובץ בדיקה")
    probe = target / ".imagesrv-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return HookResult(FAILED, f"כתיבה נכשלה: {exc}")
    return HookResult(OK)


def test_connection(kind: str, params: dict, hooks: dict, *,
                    tmp_target: Path | None = None,
                    server_base: str = "") -> dict:
    """בדיקת חיבור חיה. הצלחה = עיגון זמני + כתיבה + df שנקרא."""
    warnings: list[str] = []
    interfaces = hooks.get("interfaces", lambda: [])()
    if kind in ("nfs", "smb", "iscsi"):
        host = params.get("server") or (params.get("portal") or "").split(":")[0]
        warn = shared_nic_warning(host, interfaces, server_base)
        if warn:
            warnings.append(warn)

    if kind == "local":
        path = params.get("path") or params.get("mount_point")
        if not path:
            return {"ok": False, "reason": "חסר נתיב", "warnings": warnings}
        target = Path(path)
        if not target.is_dir():
            return {"ok": False, "reason": f"הנתיב אינו תיקייה: {path}",
                    "warnings": warnings}
        space = hooks["df"](str(target))
        if space.status != OK:
            return {"ok": False, "reason": space.reason or "לא הצלחנו לקרוא מקום פנוי",
                    "status": space.status, "warnings": warnings}
        return {"ok": True, "free_bytes": space.payload.get("free_bytes"),
                "total_bytes": space.payload.get("total_bytes"), "warnings": warnings}

    if kind == "nfs":
        server, export = params.get("server") or "", params.get("export") or ""
        if not server or not export:
            return {"ok": False, "reason": "חסר שרת או ייצוא", "warnings": warnings}
        target = tmp_target or Path("/tmp") / ("imagesrv-test-" + secrets.token_hex(3))
        target.mkdir(parents=True, exist_ok=True)
        mounted = hooks["mount"]("nfs", f"{server}:{export}", str(target), _nfs_opts(params))
        if mounted.status != OK:
            return {"ok": False, "reason": mounted.reason, "status": mounted.status,
                    "warnings": warnings}
        try:
            wrote = _write_probe(target, bool(params.get("readonly")))
            if wrote.status != OK:
                return {"ok": False, "reason": wrote.reason, "warnings": warnings}
            space = hooks["df"](str(target))
            if space.status != OK:
                return {"ok": False, "reason": space.reason or "לא הצלחנו לקרוא מקום פנוי",
                        "status": space.status, "warnings": warnings}
            return {"ok": True, "free_bytes": space.payload.get("free_bytes"),
                    "total_bytes": space.payload.get("total_bytes"), "warnings": warnings}
        finally:
            hooks["umount"](str(target))

    if kind == "smb":
        server, share = params.get("server") or "", params.get("share") or ""
        if not server or not share:
            return {"ok": False, "reason": "חסר שרת או שיתוף", "warnings": warnings}
        target = tmp_target or Path("/tmp") / ("imagesrv-test-" + secrets.token_hex(3))
        target.mkdir(parents=True, exist_ok=True)
        source = f"//{server}/{share}"
        creds = smb_creds(params)
        creds_file = creds_path("test-" + secrets.token_hex(3)) if creds else ""
        if creds_file:
            written = hooks["creds_write"](creds_file, creds)
            if written.status != OK:
                return {"ok": False, "reason": written.reason, "status": written.status,
                        "warnings": warnings}
        try:
            mounted = hooks["mount"]("cifs", source, str(target),
                                     _smb_opts(params, creds_file))
            if mounted.status != OK:
                return {"ok": False, "reason": mounted.reason, "status": mounted.status,
                        "warnings": warnings}
            try:
                wrote = _write_probe(target, bool(params.get("readonly")))
                if wrote.status != OK:
                    return {"ok": False, "reason": wrote.reason, "warnings": warnings}
                space = hooks["df"](str(target))
                if space.status != OK:
                    return {"ok": False,
                            "reason": space.reason or "לא הצלחנו לקרוא מקום פנוי",
                            "status": space.status, "warnings": warnings}
                return {"ok": True, "free_bytes": space.payload.get("free_bytes"),
                        "total_bytes": space.payload.get("total_bytes"),
                        "warnings": warnings}
            finally:
                hooks["umount"](str(target))
        finally:
            if creds_file:
                hooks["creds_remove"](creds_file)

    if kind == "iscsi":
        portal = params.get("portal") or ""
        iqn = params.get("iqn") or ""
        if not portal:
            return {"ok": False, "reason": "חסר פורטל", "warnings": warnings}
        chap = None
        if params.get("chap_user"):
            chap = {"user": params.get("chap_user"), "secret": params.get("chap_secret")}
        discovered = hooks["iscsi_discover"](portal, chap)
        if discovered.status != OK:
            return {"ok": False, "reason": discovered.reason, "status": discovered.status,
                    "warnings": warnings}
        items = (discovered.payload or {}).get("items") or []
        if iqn and not any(item.get("iqn") == iqn for item in items):
            return {"ok": False, "reason": f"היעד {iqn} לא הופיע בגילוי",
                    "warnings": warnings}
        if not items:
            return {"ok": False, "reason": "הגילוי הצליח אבל לא חזר אף יעד",
                    "warnings": warnings}
        return {"ok": True, "free_bytes": None, "total_bytes": None,
                "warnings": warnings, "targets": items}

    return {"ok": False, "reason": f"סוג לא מוכר: {kind}", "warnings": warnings}


def apply_mount(row: dict, hooks: dict) -> HookResult:
    """עיגון קבוע לפי סוג + יצירת תת-תיקיית images/."""
    params = params_of(row)
    kind = row["type"]
    target = row["mount_point"]
    if kind == "local" and is_disk(row):
        result = hooks["mount"](params.get("fs_type") or "ext4",
                                f"UUID={params['uuid']}", target, "")
    elif kind == "local":
        path = Path(target)
        if not path.is_dir():
            return HookResult(FAILED, f"הנתיב אינו תיקייה: {target}")
        return HookResult(OK)
    elif kind == "nfs":
        source = f"{params.get('server')}:{params.get('export')}"
        result = hooks["mount"]("nfs", source, target, _nfs_opts(params))
    elif kind == "smb":
        source = f"//{params.get('server')}/{params.get('share')}"
        creds = smb_creds(params)
        creds_file = creds_path(row["id"]) if creds else ""
        if creds_file:
            written = hooks["creds_write"](creds_file, creds)
            if written.status != OK:
                return written
        result = hooks["mount"]("cifs", source, target, _smb_opts(params, creds_file))
    elif kind == "iscsi":
        device = params.get("device_by_path")
        if not device:
            return HookResult(FAILED, "אין התקן — יש להתחבר ליעד קודם")
        fs_type = params.get("fs_type") or "ext4"
        result = hooks["mount"](fs_type, device, target, "")
    else:
        return HookResult(FAILED, f"סוג לא מוכר: {kind}")
    if result.status == OK:
        try:
            (Path(target) / "images").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return HookResult(FAILED, f"העיגון הצליח אבל לא נוצרה תיקיית images/: {exc}")
    return result


def fstab_entries(conn, include: str | None = None) -> list[dict]:
    """‏`include` — מיקום שמחובר עכשיו (#1993): מצבו עוד `disconnected` כשהבלוק
    נכתב, כי `connected` נקבע רק אחרי ש-fstab נכתב בהצלחה (#1123 (4))."""
    entries = []
    for row in list_rows(conn):
        if row["id"] == LOCAL_ID or (row["type"] == "local" and not is_disk(row)):
            continue
        if row["state"] == "disconnected" and row["id"] != include:
            continue
        params = params_of(row)
        if row["type"] == "local":
            # ‏#1988: לפי UUID (שם ההתקן זז בין אתחולים), ו-nofail — כונן שמת
            # לא עוצר את עליית השרת; המנטר מסמן אותו "לא נגיש".
            entries.append({
                "source": f"UUID={params['uuid']}",
                "target": row["mount_point"],
                "fstype": params.get("fs_type") or "ext4",
                "opts": "defaults,nofail,x-systemd.device-timeout=10s",
                "local": True,
            })
        elif row["type"] == "nfs":
            entries.append({
                "source": f"{params.get('server')}:{params.get('export')}",
                "target": row["mount_point"],
                "fstype": "nfs",
                "opts": _nfs_opts(params) + ",_netdev,nofail",
            })
        elif row["type"] == "smb":
            creds_file = creds_path(row["id"]) if smb_creds(params) else ""
            entries.append({
                "source": f"//{params.get('server')}/{params.get('share')}",
                "target": row["mount_point"],
                "fstype": "cifs",
                "opts": _smb_opts(params, creds_file) + ",_netdev,nofail",
            })
        elif row["type"] == "iscsi" and params.get("device_by_path"):
            entries.append({
                "source": params["device_by_path"],
                "target": row["mount_point"],
                "fstype": params.get("fs_type") or "ext4",
                "opts": "defaults,_netdev,nofail",
            })
    # ‏#1956: יעד הגיבוי (SMB/NFS) חי באותו בלוק — כל כתיבה מחדש של fstab שומרת אותו.
    from server.system.backup_target import fstab_entry   # noqa: PLC0415 — ‏backup_target מייבא את המודול הזה
    backup = fstab_entry(conn)
    if backup:
        entries.append(backup)
    return entries


def rewrite_fstab(conn, hooks: dict, include: str | None = None) -> HookResult:
    return hooks["fstab_write"](fstab_entries(conn, include))


# ---------------------------------------------------------------------------
# מנטר — כל 60ש', רק connected/unreachable
# ---------------------------------------------------------------------------

#: ‏#1994: סוגים שהם עיגון (תמיד עם `nofail`) — לא תיקייה כמו `loc_local`.
MOUNTED_TYPES = ("nfs", "smb", "iscsi")


def measure(row: dict, hooks: dict) -> HookResult:
    """‏df של מיקום — רק כשהוא באמת מעוגן: ‏df על נקודת עיגון ריקה מודד את
    דיסק המערכת ו"מצליח", ומיקום שלא עלה באתחול (nofail) היה נראה "מחובר"
    עם מספרים של דיסק אחר. כונן (#1988) ומיקום רשת (#1994) כאחד."""
    disk = is_disk(row)
    if (disk or row["type"] in MOUNTED_TYPES) and             not hooks.get("is_mount", os.path.ismount)(row["mount_point"]):
        return HookResult(FAILED, "הכונן אינו מעוגן" if disk else "המיקום אינו מעוגן")
    return hooks["df"](row["mount_point"])


#: ‏#2123: מה שהרשימה מראה בין עליית השרת לבדיקה הראשונה שלו.
AFTER_START_DETAIL = "לא נבדק מאז הפעלת השרת"


def mark_unchecked_after_start(conn) -> int:
    """‏#2123: מצב שמור של עיגון אינו ראיה אחרי restart או אתחול — העיגון יכול
    היה להיעלם (#2122) או לחזור (fstab). עד הבדיקה הראשונה של התהליך הזה
    מיקום-עיגון הוא `unchecked`, לא "מחובר" (ולא "לא נגיש") מהזיכרון; וגם
    הספרייה אינה כותבת אליו בינתיים (`library_roots`). מחזיר כמה סומנו."""
    marked = 0
    for row in list_rows(conn):
        if row["state"] in ("connected", "unreachable") and \
                (is_disk(row) or row["type"] in MOUNTED_TYPES):
            set_state(conn, row["id"], "unchecked", AFTER_START_DETAIL, force_since=True)
            marked += 1
    return marked


def has_unchecked(conn) -> bool:
    return any(row["state"] == "unchecked" for row in list_rows(conn))


def poll(conn, library, hooks: dict) -> None:
    """df/stat על כל מיקום מחובר/לא-נגיש. disconnected לא נבדק.

    לעולם אינו זורק — דגימה שנכשלה נרשמת וממשיכה (כמו room.sweep).
    """
    try:
        _poll(conn, library, hooks)
    except Exception:  # noqa: BLE001
        log.exception("storage location poll failed")


def _poll(conn, library, hooks: dict) -> None:
    refresh_catalog(conn, library)
    for row in list_rows(conn):
        if row["state"] == "disconnected":
            continue
        result = measure(row, hooks)
        if result.status == OK:
            payload = result.payload or {}
            save_df(conn, row["id"], payload.get("free_bytes"), payload.get("total_bytes"))
            if row["state"] != "connected":
                set_state(conn, row["id"], "connected", "", force_since=True)
                journal(conn, "storage_location_connected",
                        f"{row['id']} {row['name']}")
            else:
                set_state(conn, row["id"], "connected", "")
        else:
            detail = result.reason or "הבדיקה נכשלה"
            if row["state"] != "unreachable":
                set_state(conn, row["id"], "unreachable", detail, force_since=True)
                journal(conn, "storage_location_unreachable",
                        f"{row['id']} {row['name']} — {detail}")
            else:
                set_state(conn, row["id"], "unreachable", detail)
    refresh_catalog(conn, library)


def health_checks(ctx) -> list[dict]:
    """שורה לכל מיקום במסך הבריאות — שלושה מצבים גלויים."""
    from server.system.health import check
    rows = []
    try:
        locations = list_rows(ctx.conn)
    except Exception:  # noqa: BLE001 — טבלה חסרה אינה "אין מיקומים"
        return [check("storage_locations", "מיקומי אחסון", "unknown",
                      "לא ניתן לקרוא את טבלת המיקומים")]
    library = ctx.library
    for row in locations:
        loc_state = row["state"]
        if loc_state == "connected":
            light = "ok"
        elif loc_state == "unreachable":
            light = "bad"
        elif loc_state == "disconnected":
            light = "off"
        else:
            light = "unknown"
        info = last_df(row)
        total, unavailable = image_counts(library, row["id"])
        bits = [STATE_LABELS.get(loc_state, loc_state)]
        if info.get("free_bytes") is not None and info.get("total_bytes"):
            bits.append(f"{info['free_bytes']} פנוי מתוך {info['total_bytes']}")
        bits.append(f"{total} אימג'ים")
        if unavailable:
            bits.append(f"{unavailable} לא זמינים")
        if row["state_detail"]:
            bits.append(row["state_detail"])
        rows.append(check(
            f"storage_{row['id']}", row["name"], light, " · ".join(bits)))
    return rows
