"""#1920 -- which block device is the clone stick, and how it is opened.

**Positive evidence only, never a guess.** A partition is the stick's data partition when
ALL of these hold, read from one ``lsblk -J`` tree (``classify``):

* its label is exactly ``IMGSRV_DATA`` and it is FAT;
* its parent disk is on the USB bus (``tran == usb``) -- a disk the hypervisor hands to the
  guest as a SCSI disk is not accepted on a label alone (the Hyper-V case, see
  docs/architecture/interfaces.md, "הדיסק-און-קי בשרת");
* the same disk has the ``IMGSRV_USB`` partition (the stick's boot partition) and no second
  ``IMGSRV_DATA``;
* the disk is writable media, and nothing has it mounted.

Anything else is named and left alone (``unsafe``): a label is not the stick (#2431 -- the agent
once mounted an internal drawer labelled IMGSRV_DATA read-write). The device is **identified by
its USB serial, or by its by-id link**, never by ``/dev/sdX`` (#1807): the node name is only
the handle of this one moment, checked again by ``open_stick`` right before the mount.

Opening is read-only by default. ``write=True`` is for the one sync the operator confirmed, and
what is written is the caller's business (``sync``): this module only mounts and unmounts.
mount/umount run in the host's mount namespace (``storage.locations``, #2122), so a service
restart cannot leave the stick mounted in a namespace nobody sees.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from server.storage import locations as sl

DATA_LABEL = "IMGSRV_DATA"
BOOT_LABEL = "IMGSRV_USB"
#: inside /media -- the unit's ReadWritePaths (deploy/imagesrv-server.service) -- one fixed place.
MOUNT_POINT = "/media/imagesrv-usb"
LSBLK_COLUMNS = "NAME,PKNAME,PATH,TYPE,SIZE,MODEL,VENDOR,SERIAL,RO,RM,TRAN,FSTYPE,LABEL,UUID,MOUNTPOINTS"
BY_ID_DIR = "/dev/disk/by-id"
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")

NONE, CONNECTED, AMBIGUOUS, UNSAFE, UNREAD = "none", "connected", "ambiguous", "unsafe", "unread"


@dataclass(frozen=True)
class Stick:
    device_id: str                  # "usb-<serial>" or "usb-<fat uuid>": what a record says it came from
    label: str
    fstype: str
    size_bytes: int | None
    vendor: str
    model: str
    partition: str                  # the node of this moment -- not an identity
    by_id: str | None               # /dev/disk/by-id/usb-..-part2, stable across replug
    has_serial: bool

    def public(self) -> dict:
        return {"device_id": self.device_id, "label": self.label, "fstype": self.fstype,
                "size_bytes": self.size_bytes, "vendor": self.vendor, "model": self.model,
                "has_serial": self.has_serial}


@dataclass
class Scan:
    state: str                      # none | connected | ambiguous | unsafe | unread
    stick: Stick | None = None
    reason: str = ""                # Hebrew, what the operator should read
    notes: list[str] = field(default_factory=list)


class StickError(Exception):
    """The stick could not be opened or closed safely -- the message is for the operator."""


def _flag(value: object) -> bool:
    return value in (True, 1, "1", "true")


def flatten(tree: dict) -> list[dict] | None:
    """Both shapes lsblk 2.41 returns -- a tree with ``children`` and a flat list -- as one flat
    list of nodes with ``pkname``. None = the output is not a device tree at all (#2102)."""
    pending = tree.get("blockdevices") if isinstance(tree, dict) else None
    if not isinstance(pending, list):
        return None
    nodes = []
    pending = list(pending)
    while pending:
        node = pending.pop(0)
        if not isinstance(node, dict):
            return None
        children = node.get("children") or []
        if not isinstance(children, list):
            return None
        nodes.append(node)
        for child in children:
            if isinstance(child, dict) and not child.get("pkname"):
                child = {**child, "pkname": node.get("name")}
            pending.append(child)
    return nodes


def _mountpoints(node: dict) -> list[str]:
    points = node.get("mountpoints")
    if points is None:
        points = [node.get("mountpoint")]
    return [str(p) for p in points if p]


def classify(tree: dict, by_id: dict[str, str] | None = None) -> Scan:
    """One lsblk tree -> what is plugged in. ``by_id`` maps a node path to its by-id link."""
    nodes = flatten(tree)
    if nodes is None:
        return Scan(UNREAD, reason="פלט lsblk אינו עץ התקנים — לא הצלחתי לבדוק")
    by_name = {n.get("name"): n for n in nodes if n.get("name")}
    candidates = [n for n in nodes if n.get("type") == "part" and str(n.get("label") or "") == DATA_LABEL]
    if not candidates:
        return Scan(NONE)
    good, refused = [], []
    for part in candidates:
        why = _refusal(part, by_name, nodes)
        (refused if why else good).append((part, why))
    notes = [why for _, why in refused]
    if len(good) > 1:
        return Scan(AMBIGUOUS, notes=notes, reason=f"מחוברים {len(good)} דיסק-און-קי עם התווית {DATA_LABEL} — נתקו את המיותרים ובדקו מחדש")
    if not good:
        return Scan(UNSAFE, reason=notes[0], notes=notes[1:])
    part = good[0][0]
    disk = by_name[part["pkname"]]
    serial = str(disk.get("serial") or "").strip()
    uuid = str(part.get("uuid") or "").strip()
    ident = serial or uuid
    device_id = "usb-" + _SAFE_ID.sub("_", ident)[:64]
    path = str(part.get("path") or f"/dev/{part.get('name')}")
    size = part.get("size")
    stick = Stick(
        device_id=device_id, label=DATA_LABEL, fstype=str(part.get("fstype") or ""),
        size_bytes=int(size) if isinstance(size, (int, str)) and str(size).isdigit() else None,
        vendor=str(disk.get("vendor") or "").strip(), model=str(disk.get("model") or "").strip(),
        partition=path, by_id=(by_id or {}).get(path), has_serial=bool(serial))
    return Scan(CONNECTED, stick=stick, notes=notes)


def _refusal(part: dict, by_name: dict, nodes: list[dict]) -> str:
    """'' = this partition is the stick's data partition; else why it is left alone."""
    where = str(part.get("path") or part.get("name"))
    disk = by_name.get(part.get("pkname"))
    if disk is None or disk.get("type") != "disk":
        return f"{where}: לא נמצא הדיסק שהמחיצה {DATA_LABEL} שייכת לו — לא נגעתי בה"
    tran = str(disk.get("tran") or "").strip()
    if tran != "usb":
        return (f"{where}: יש מחיצה בשם {DATA_LABEL}, אבל הדיסק שלה אינו על אפיק USB "
                f"({tran or 'האפיק לא ידוע'}) — לא נגעתי בה. תווית אינה ראיה שזה דיסק-און-קי")
    if str(part.get("fstype") or "") != "vfat":
        return f"{where}: מערכת הקבצים אינה FAT ({part.get('fstype') or 'לא ידועה'}) — לא נגעתי בה"
    if _flag(disk.get("ro")) or _flag(part.get("ro")):
        return f"{where}: ההתקן מסומן לקריאה בלבד"
    siblings = [n for n in nodes if n.get("pkname") == disk.get("name") and n.get("type") == "part"]
    labels = [str(n.get("label") or "") for n in siblings]
    if BOOT_LABEL not in labels:
        return f"{where}: על הדיסק אין מחיצת {BOOT_LABEL} — זה לא דיסק-און-קי של ImageSrv"
    if labels.count(DATA_LABEL) != 1:
        return f"{where}: יש על הדיסק יותר ממחיצה אחת בשם {DATA_LABEL}"
    mounted = _mountpoints(part) + [p for n in siblings for p in _mountpoints(n)]
    if mounted:
        return f"{where}: ההתקן כבר מעוגן ב-{mounted[0]} — לא עוגנתי אותו שוב"
    if not (str(disk.get("serial") or "").strip() or str(part.get("uuid") or "").strip()):
        return f"{where}: אין למחיצה מזהה (serial או UUID) — אי אפשר לרשום ממנו רשומות"
    return ""


def _by_id_links(directory: str = BY_ID_DIR) -> dict[str, str]:
    """{node path: by-id link} for the usb-* links; {} when the directory is not there."""
    out: dict[str, str] = {}
    try:
        for name in os.listdir(directory):
            if name.startswith("usb-"):
                link = os.path.join(directory, name)
                out[os.path.realpath(link)] = link
    except OSError:
        return {}
    return out


def scan(run=None, by_id=None) -> Scan:
    """The real machine. Cannot run lsblk / cannot read it = ``unread`` ("not checked"), never
    ``none`` ("nothing plugged in")."""
    run = run or sl._run
    listed = run(["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS], timeout=20)
    if listed.status != sl.OK:
        return Scan(UNREAD, reason=f"לא הצלחתי להריץ lsblk: {listed.reason}")
    try:
        tree = json.loads((listed.payload or {}).get("stdout") or "")
    except ValueError:
        return Scan(UNREAD, reason="פלט lsblk אינו JSON — לא הצלחתי לבדוק")
    return classify(tree, (by_id or _by_id_links)())


@contextmanager
def open_stick(stick: Stick, *, write: bool = False, scanner=None, mount=None, umount=None, mountpoint: str = MOUNT_POINT):
    """Mount the stick's data partition and yield its root as a Path; unmount on the way out.

    Checked again right now -- the device may have been unplugged and another plugged since the
    page was drawn: the same ``device_id``, and still safe. Read-only unless ``write``."""
    scanner = scanner or scan
    mount = mount or sl.mount
    umount = umount or sl.umount
    seen = scanner()
    if seen.state != CONNECTED or seen.stick is None:
        raise StickError(seen.reason or "הדיסק-און-קי כבר אינו מחובר")
    if seen.stick.device_id != stick.device_id:
        raise StickError("הדיסק-און-קי שהוחלף אינו זה שהוצג — בדקו מחדש")
    source = seen.stick.by_id if seen.stick.by_id and os.path.exists(seen.stick.by_id) else seen.stick.partition
    if sl.is_mounted(mountpoint):
        raise StickError(f"נקודת העיגון {mountpoint} תפוסה — לא עוגנתי עליה")
    opts = ("rw,sync,nosuid,nodev,noexec,umask=0022" if write else "ro,nosuid,nodev,noexec,umask=0222")
    got = mount("vfat", source, mountpoint, opts)
    if got.status != sl.OK:
        raise StickError(f"העיגון נכשל: {got.reason}")
    try:
        yield Path(mountpoint)
    finally:
        gone = umount(mountpoint)
        if gone.status != sl.OK:
            raise StickError(f"ההתקן נשאר מעוגן ({mountpoint}): {gone.reason} — אל תנתקו אותו לפני שהמעגן שוחרר")


def default_hooks() -> dict:
    """The real stick. Tests inject ``{"scan": ..., "open": ...}`` (a directory, no device)."""
    return {"scan": scan, "open": open_stick}
