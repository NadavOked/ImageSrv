"""‏#1897 — הכרעת נדב (22/10): המקום שהשחזור מוסיף מתחלק לפי היחס במקור.

אימג' C: + D: שנקלט עם כיווץ (#87/#929): שתיהן מכווצות במניפסט. עד כאן
‏expand_last מתח את C: (המועמד) עד תחילת הזנב, ו-D: עברה לסוף הדיסק בגודלה
המכווץ — כ-2GB פנויים בכל מחשב, לתמיד. עכשיו: במקור C:=60 ו-D:=40, וכך
מתחלק גם המקום שנוסף ביעד. מחיצה שלא כווצה (recovery) נשארת בגודלה.

ו-min-free (#1354) רואה את אותו חלק: ‏C: מקבלת רק את חלקה, והתאומים —
‏`system_free_after` בסוכן ו-`minfree.free_after` בשרת — אומרים אותו מספר.
"""

from __future__ import annotations

import json
import shutil

import pytest

from server import minfree
from server.images import required_bytes

from test_agent import AGENT, BASH, posix, run_expand, sh, tail_n

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GIB = 1 << 30
DISK = 976773168                  # ‏500GB, בסקטורים
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
C_START = 239616


def c_and_d() -> dict:
    """ESP, ‏C: ‏60→20GiB, ‏D: ‏40→10GiB, ואז recovery שלא כווצה."""
    d_start = C_START + 20 * GIB // 512
    rec_start = d_start + 10 * GIB // 512
    parts = [
        dict(index=1, type_guid=ESP, role="esp", fs="vfat", start_sector=2048,
             size_bytes=100 << 20, used_bytes=30 << 20),
        dict(index=3, type_guid=WIN, role="windows", fs="ntfs", start_sector=C_START,
             size_bytes=20 * GIB, used_bytes=15 * GIB, shrunk_from_bytes=60 * GIB,
             system=True, expandable=True),
        dict(index=4, type_guid=WIN, role="data", fs="ntfs", start_sector=d_start,
             size_bytes=10 * GIB, used_bytes=8 * GIB, shrunk_from_bytes=40 * GIB,
             source_start_sector=C_START + 60 * GIB // 512),
        dict(index=5, type_guid=REC, role="recovery", fs="ntfs", start_sector=rec_start,
             size_bytes=800 << 20, used_bytes=500 << 20),
    ]
    for p in parts:
        p.setdefault("expandable", False)
        p.update(file=f"p{p['index']}.x.pcl.zst", sha256="aa" * 32,
                 unique_guid="", uuid="", attrs="")
    m = {"schema": 1, "id": "img_1897", "name": "t", "family": 256, "scheme": "gpt",
         "sector_size": 512, "disk_guid": None, "source_disk_bytes": None, "partitions": parts}
    m["min_target_bytes"] = required_bytes(m)
    return m


def plan_of(m: dict) -> list[str]:
    keys = ("index", "type_guid", "role", "fs", "start_sector", "size_bytes", "file", "sha256")
    return ["|".join(str(p[k]) for k in keys)
            + f"|{str(p['expandable']).lower()}|{p['unique_guid']}|{p['uuid']}|{p['attrs']}"
            for p in m["partitions"]]


@needs_shell
def test_d_takes_its_source_share_of_the_room_and_recovery_keeps_its_size(tmp_path):
    m = c_and_d()
    rc, calls, marker = run_expand(tmp_path, plan_of(m), disk_sectors=DISK, manifest=m)
    assert rc == "rc=0", (rc, calls)
    assert marker == "3|ntfs"
    table_end = C_START + 20 * GIB // 512
    tail = 10 * GIB // 512 + (800 << 20) // 512
    room = DISK - 34 - table_end - tail
    d_grow = room * 40 // 100 // 2048 * 2048
    d_sectors = 10 * GIB // 512 + d_grow
    rec_n, rec_start = tail_n(5, (800 << 20) // 512)
    d_n, _ = tail_n(4, d_sectors, end=rec_start - 1)
    call = calls[0]
    assert f" {rec_n} " in f" {call} ", "‏recovery זזה לזנב בגודלה — לא כווצה, לא גדלה"
    assert f" {d_n} " in f" {call} ", f"‏D: לא קיבלה 40% מהמקום: {call}"
    assert f" -d 3 -n 3:{C_START}:0 " in call, "‏C: נמתחת עד D: ולוקחת את השאר (≈60%)"
    more = (tmp_path / "box/run/targets/sda/expanded.more").read_text().strip()
    assert more == "4|ntfs", "‏D: חייבת לגדול גם כמערכת קבצים אחרי הזרם"


@needs_shell
def test_an_image_without_a_shrunk_tail_expands_exactly_as_before(tmp_path):
    m = c_and_d()
    for p in m["partitions"]:
        p.pop("shrunk_from_bytes", None)
    rc, calls, _ = run_expand(tmp_path, plan_of(m), disk_sectors=DISK, manifest=m)
    assert rc == "rc=0"
    rec_n, rec_start = tail_n(5, (800 << 20) // 512)
    d_n, _ = tail_n(4, 10 * GIB // 512, end=rec_start - 1)
    assert f" {d_n} " in f" {calls[0]} "
    more = tmp_path / "box/run/targets/sda/expanded.more"
    assert not more.exists() or more.read_text() == ""


def _grow(tmp_path, fail_on: str = "") -> tuple[str, str]:
    run = tmp_path / "run"
    (run / "targets/sda").mkdir(parents=True)
    (run / "targets/sda/expanded").write_text("3|ntfs\n")
    (run / "targets/sda/expanded.more").write_text("4|ntfs\n")
    out = sh(f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/grow.sh; . {posix(AGENT)}/lib/ntfsunhib.sh; . {posix(AGENT)}/lib/expandshare.sh; "
             f'grow_filesystem() {{ echo "$1 $2" >> {posix(run)!r}/grown; '
             f'[ "$2" != {fail_on!r} ] || {{ GROW_ERROR=boom; return 1; }}; }}; '
             'grow_expanded sda; echo "rc=$? err=$GROW_ERROR"')
    return out.strip().splitlines()[-1], (run / "grown").read_text()


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_after_the_stream_d_grows_too_and_a_failure_names_it(tmp_path):
    rc, grown = _grow(tmp_path / "ok")
    assert rc == "rc=0 err="
    assert [line.rsplit("/", 1)[-1] for line in grown.split("\n")[:2]] == ["sda3", "sda4"]
    assert grown.startswith("ntfs ")
    rc, _ = _grow(tmp_path / "bad", fail_on=posix(tmp_path / "bad/run") + "/dev/sda4")
    assert rc == "rc=1 err=מחיצה 4 (ntfs): boom"


# --- min-free: ‏C: מקבלת רק את חלקה, בסוכן ובשרת ----------------------------


def _agent_free(tmp_path, m: dict, disk: int) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path)!r}/log; "
             + "".join(f". {posix(AGENT)}/lib/{lib}.sh; " for lib in
                       ("common", "jsonq", "progress", "restore", "expand", "diskfit"))
             + f'system_free_after {posix(path)!r} {disk} {required_bytes(m)} 3')
    return out.strip().splitlines()[-1]


@needs_shell
@pytest.mark.parametrize("disk", [DISK * 512, 256060514304, 128 * GIB])
def test_the_agent_and_the_server_give_c_the_same_share(tmp_path, disk):
    m = c_and_d()
    free, role = minfree.free_after(m, disk)
    grow = disk - required_bytes(m)
    expect = 5 * GIB + (int(grow * 0.6) if grow > minfree.EXPAND_THRESHOLD else 0)
    assert abs(free - expect) <= 1, (free, expect)   # float של 0.6 — הבייט האחרון
    assert _agent_free(tmp_path / str(disk), m, disk) == f"{free}|windows"


def test_the_smallest_disk_that_fits_counts_only_c_s_share():
    """‏C: צריכה עוד 15GiB כדי להגיע ל-20 פנויים; היא מקבלת 60% מהמקום — לכן
    הדיסק צריך 25GiB מעל הפריסה, לא 15."""
    m = c_and_d()
    need = required_bytes(m)
    smallest = minfree.min_disk_bytes(m, 20)
    assert abs(smallest - (need + -(-15 * GIB * 100 // 60))) <= 2
    assert minfree.refusal(m, smallest, 20) is None
    assert minfree.refusal(m, smallest - 1, 20) is not None
