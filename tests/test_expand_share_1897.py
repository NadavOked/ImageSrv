"""#1897 — הכרעת נדב (04/11): כל כונן חוזר לגודלו במקור, ורק C: מתפשרת.

אימג' C: + D: + E: שנקלט עם כיווץ (#87/#929): שלושתן מכווצות במניפסט. עד
22/10 D: הגיעה לכל יעד מכווצת, עם כ-2GB פנויים, לתמיד; מ-22/10 המקום הנוסף
התחלק לפי היחס במקור. ההכרעה של 04/11 מחליפה את שתיהן:

1. בקליטה מכווצים את כל הכוננים (כבר כך מ-#929 — לא נבדק כאן).
2. בשחזור כל כונן חוזר לגודלו המקורי (`shrunk_from_bytes`).
3. דיסק גדול מהמקור: D: ו-E: בגודלן המקורי, וכל המקום הנוסף ל-C:.
4. דיסק קטן מהמקור: רק C: קטנה, עד המקום הפנוי המינימלי שלה; D: ו-E: בגודלן
   המקורי. לא מספיק — השיכפול לא מתחיל, עם סיבה, לפני הבייט הראשון (diskfit).

הטסטים כאן מודדים את **גודל כל כונן ביעד** — לא רק שהוא זז — על ארבעה יעדים:
גדול (500GB), שווה למקור (256GB), קטן שעוד מספיק (160GB) וקטן שאינו מספיק
(128GB). והתאומים — הסוכן (jq, `cand_room`) והשרת (`minfree.cand_room`) —
אומרים את אותו גודל ל-C: כמו קריאת ה-sgdisk ש-`expand_last` כותב בפועל.
"""

from __future__ import annotations

import json
import shutil

import pytest

from server.library import minfree
from server.library.images import required_bytes

from test_agent import AGENT, BASH, _stub, posix, run_expand, sh, tail_n
from agentlib import flat_lib

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GIB = 1 << 30
MIB = 1 << 20
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"

#: המקור: 256GB. C: 120→30GiB (20 תפוסים), recovery 800MiB שלא כווצה,
#: D: 100→12GiB (10 תפוסים), E: 15→4GiB (2 תפוסים) — בפריסה הצפופה של הקליטה.
C_START = 206848
C_NOW, C_ORIG = 30 * GIB, 120 * GIB
REC_SIZE = 800 * MIB
D_NOW, D_ORIG = 12 * GIB, 100 * GIB
E_NOW, E_ORIG = 4 * GIB, 15 * GIB
REC_START = C_START + C_NOW // 512
D_START = REC_START + REC_SIZE // 512
E_START = D_START + D_NOW // 512

DISK_500 = 976773168
DISK_256 = 500118192      # המקור עצמו
DISK_160 = 312581808
DISK_128 = 250069680
FITTING = (DISK_500, DISK_256, DISK_160)


def c_d_e() -> dict:
    parts = [
        dict(index=1, type_guid=ESP, role="esp", fs="vfat", start_sector=2048,
             size_bytes=100 * MIB, used_bytes=30 * MIB),
        dict(index=2, type_guid=WIN, role="windows", fs="ntfs", start_sector=C_START,
             size_bytes=C_NOW, used_bytes=20 * GIB, shrunk_from_bytes=C_ORIG,
             system=True, expandable=True),
        dict(index=3, type_guid=REC, role="recovery", fs="ntfs", start_sector=REC_START,
             size_bytes=REC_SIZE, used_bytes=500 * MIB,
             source_start_sector=C_START + C_ORIG // 512),
        dict(index=4, type_guid=WIN, role="data", fs="ntfs", start_sector=D_START,
             size_bytes=D_NOW, used_bytes=10 * GIB, shrunk_from_bytes=D_ORIG,
             source_start_sector=C_START + C_ORIG // 512 + REC_SIZE // 512),
        dict(index=5, type_guid=WIN, role="data", fs="ntfs", start_sector=E_START,
             size_bytes=E_NOW, used_bytes=2 * GIB, shrunk_from_bytes=E_ORIG,
             source_start_sector=C_START + (C_ORIG + REC_SIZE + D_ORIG) // 512),
    ]
    for p in parts:
        p.setdefault("expandable", False)
        p.update(file=f"p{p['index']}.x.pcl.zst", sha256="aa" * 32,
                 unique_guid="", uuid="", attrs="")
    m = {"schema": 1, "id": "img_1897", "name": "t", "family": 256, "scheme": "gpt",
         "sector_size": 512, "disk_guid": None, "source_disk_bytes": DISK_256 * 512,
         "partitions": parts}
    m["min_target_bytes"] = required_bytes(m)
    return m


def plan_of(m: dict) -> list[str]:
    keys = ("index", "type_guid", "role", "fs", "start_sector", "size_bytes", "file", "sha256")
    return ["|".join(str(p[k]) for k in keys)
            + f"|{str(p['expandable']).lower()}|{p['unique_guid']}|{p['uuid']}|{p['attrs']}"
            for p in m["partitions"]]


def layout(disk: int) -> dict:
    """מה שהיעד אמור לקבל: הזנב נארז מסוף הדיסק (_move_to_tail), כל אחת בגודלה
    במקור, ו-C: מ-C_START עד תחילת ה-recovery."""
    e_n, e_start = tail_n(5, E_ORIG // 512, end=disk - 34)
    d_n, d_start = tail_n(4, D_ORIG // 512, end=e_start - 1)
    r_n, r_start = tail_n(3, REC_SIZE // 512, end=d_start - 1)
    return {"args": (e_n, d_n, r_n), "c_sectors": r_start - C_START}


@needs_shell
@pytest.mark.parametrize("disk", FITTING, ids=["500GB", "256GB-equal", "160GB-smaller"])
def test_d_and_e_get_their_source_size_and_c_takes_the_rest(tmp_path, disk):
    m = c_d_e()
    rc, calls, marker = run_expand(tmp_path, plan_of(m), disk_sectors=disk, manifest=m)
    assert rc == "rc=0", (rc, calls)
    assert marker == "2|ntfs"
    want = layout(disk)
    e_n, d_n, r_n = want["args"]
    call = f" {calls[0]} "
    assert f" {e_n} " in call, f"\u200fE: לא חזרה ל-15GiB בדיוק: {call}"
    assert f" {d_n} " in call, f"\u200fD: לא חזרה ל-100GiB בדיוק: {call}"
    assert f" {r_n} " in call, "\u200frecovery לא כווצה — זזה לזנב בגודלה"
    assert f" -d 2 -n 2:{C_START}:0 " in call, "\u200fC: נמתחת עד ה-recovery ולוקחת את כל השאר"
    c_bytes = want["c_sectors"] * 512
    assert c_bytes >= C_NOW
    if disk >= DISK_256:
        assert c_bytes >= C_ORIG, "יעד שאינו קטן מהמקור מחזיר גם ל-C: לפחות את גודלה במקור"
    if disk == DISK_160:
        assert c_bytes < C_ORIG, "יעד קטן: רק C: מתפשרת"
    more = (tmp_path / "box/run/targets/sda/expanded.more").read_text().split()
    assert more == ["4|ntfs", "5|ntfs"], "\u200fD: ו-E: גדלות גם כמערכת קבצים אחרי הזרם"


@needs_shell
def test_expand_writes_no_table_where_c_would_be_smaller_than_its_image(tmp_path):
    """disk_fits דוחה את הדיסק הזה קודם; expand_last לא סומך על כך."""
    m = c_d_e()
    rc, calls, marker = run_expand(tmp_path, plan_of(m), disk_sectors=DISK_128, manifest=m)
    assert rc == "rc=1" and calls == [] and marker is None, (rc, calls)


@needs_shell
def test_an_image_without_a_shrunk_tail_expands_exactly_as_before(tmp_path):
    m = c_d_e()
    for p in m["partitions"]:
        p.pop("shrunk_from_bytes", None)
    rc, calls, _ = run_expand(tmp_path, plan_of(m), disk_sectors=DISK_500, manifest=m)
    assert rc == "rc=0"
    e_n, e_start = tail_n(5, E_NOW // 512)
    d_n, _ = tail_n(4, D_NOW // 512, end=e_start - 1)
    assert f" {e_n} " in f" {calls[0]} " and f" {d_n} " in f" {calls[0]} "
    more = tmp_path / "box/run/targets/sda/expanded.more"
    assert not more.exists() or more.read_text() == ""


@needs_shell
def test_an_image_without_a_shrunk_tail_keeps_the_1gib_threshold(tmp_path):
    """בלי מחיצה שחוזרת — הסף של expand_last כמו קודם: אין מה להרוויח, אין נגיעה."""
    m = c_d_e()
    for p in m["partitions"]:
        p.pop("shrunk_from_bytes", None)
    end = E_START + E_NOW // 512
    rc, calls, marker = run_expand(tmp_path, plan_of(m), disk_sectors=end + 2048 + 34, manifest=m)
    assert rc == "rc=0" and calls == [] and marker is None


def _grow(tmp_path, fail_on: str = "") -> tuple[str, str]:
    run = tmp_path / "run"
    (run / "targets/sda").mkdir(parents=True)
    (run / "targets/sda/expanded").write_text("2|ntfs\n", newline="\n")
    (run / "targets/sda/expanded.more").write_text("4|ntfs\n5|ntfs\n", newline="\n")   # CRLF = "ntfs\r"
    out = sh(f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/restore/grow.sh; . {posix(AGENT)}/lib/restore/ntfsunhib.sh; . {posix(AGENT)}/lib/restore/expandshare.sh; "
             f'grow_filesystem() {{ echo "$1 $2" >> {posix(run)!r}/grown; '
             f'[ "$2" != {fail_on!r} ] || {{ GROW_ERROR=boom; return 1; }}; }}; '
             'grow_expanded sda; echo "rc=$? err=$GROW_ERROR"')
    return out.strip().splitlines()[-1], (run / "grown").read_text()


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_after_the_stream_d_and_e_grow_too_and_a_failure_names_it(tmp_path):
    rc, grown = _grow(tmp_path / "ok")
    assert rc == "rc=0 err="
    assert [line.rsplit("/", 1)[-1] for line in grown.split("\n")[:3]] == ["sda2", "sda4", "sda5"]
    rc, _ = _grow(tmp_path / "bad", fail_on=posix(tmp_path / "bad/run") + "/dev/sda4")
    assert rc == "rc=1 err=מחיצה 4 (ntfs): boom"


# --- לפני הבייט הראשון: disk_fits, min-free, והשרת -----------------------------

LIBS = ("common", "jsonq", "sysinfo", "diskinfo", "progress", "failmark", "restore", "expand", "diskfit")


def _disk_fits(tmp_path, m: dict, disk_bytes: int, min_free_gb: int) -> tuple[str, str]:
    run = tmp_path / "run"; (run / "targets/sda").mkdir(parents=True)
    (run / "targets/sda/state").write_text("writing\n")
    (run / "min_free_gb").write_text(f"{min_free_gb}\n")
    (tmp_path / "m.json").write_text(json.dumps(m), encoding="utf-8")
    stubs = tmp_path / "stubs"; stubs.mkdir()
    out = sh(_stub(stubs / "blockdev", f"echo {disk_bytes}")
             + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + "raid_member_reason() { :; }; "
             + f'disk_fits sda {posix(tmp_path / "m.json")!r}; echo "rc=$?"')
    err = run / "targets/sda/error"
    return out.strip().splitlines()[-1], err.read_text(encoding="utf-8") if err.exists() else ""


@needs_shell
def test_a_disk_too_small_for_d_and_e_is_refused_by_name_before_the_table(tmp_path):
    rc, err = _disk_fits(tmp_path, c_d_e(), DISK_128 * 512, 0)
    assert rc == "rc=1"
    assert "מחיצה 4 חוזרת לגודלה במקור, 107.3 GB" in err, err
    assert "מחיצה 5 חוזרת לגודלה במקור, 16.1 GB" in err, err
    assert "למחיצה 2 נשארים" in err and "והיא צריכה 32.2 GB" in err, err
    assert err.startswith("הדיסק קטן מדי ב-") and " MiB" in err


@needs_shell
def test_on_the_same_disk_an_image_where_nothing_was_shrunk_is_ok(tmp_path):
    """The answer the jq stub of test_restore_evidence.py gives volumes_fit: on a
    manifest without shrunk_from_bytes, real jq prints ok -- nothing goes back."""
    m = c_d_e()
    for p in m["partitions"]:
        p.pop("shrunk_from_bytes", None)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + f'volumes_fit {posix(path)!r} {DISK_128 * 512} 2')
    assert out.strip().splitlines()[-1] == "ok", out


@needs_shell
def test_a_smaller_disk_shrinks_only_c_down_to_its_min_free(tmp_path):
    free = layout(DISK_160)["c_sectors"] * 512 - 20 * GIB
    assert 10 * GIB < free < 20 * GIB
    rc, err = _disk_fits(tmp_path / "ten", c_d_e(), DISK_160 * 512, 10)
    assert rc == "rc=0", err
    rc, err = _disk_fits(tmp_path / "twenty", c_d_e(), DISK_160 * 512, 20)
    assert rc == "rc=1"
    assert f"יישארו רק {free / GIB:.1f} GB פנויים ב-C:" in err, err


@needs_shell
@pytest.mark.parametrize("disk", FITTING + (DISK_128,), ids=["500GB", "256GB", "160GB", "128GB"])
def test_the_agent_and_the_server_give_c_what_expand_writes(tmp_path, disk):
    m = c_d_e()
    cand = minfree.expandable_candidate(m)
    if disk != DISK_128:
        assert minfree.cand_room(m, cand, disk) == layout(disk)["c_sectors"]
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + f'system_free_after {posix(path)!r} {disk * 512} {required_bytes(m)} 2')
    free, role = minfree.free_after(m, disk * 512)
    assert role == "windows"
    assert free == minfree.cand_room(m, cand, disk) * 512 - 20 * GIB
    assert out.strip().splitlines()[-1] == f"{free}|windows"


@needs_shell
@pytest.mark.parametrize("gb", [0, 20])
def test_the_smallest_disk_the_server_offers_is_the_one_the_agent_accepts(tmp_path, gb):
    """"נכנס לדיסק מ-X" של הקונסולה — הבייט שבו הסוכן מתחיל לקבל, לא לפניו.

    gb=0: סוף הפריסה עם D: ו-E: בגודלן במקור, ועוד **33 סקטורים** — ה-GPT
    המשני (כותרת + 32 של רשומות; הסקטור השמיש האחרון הוא D-34, ושם
    _move_to_tail מסיים את E:). לא `required_bytes` + ההפרשים: ה-256KiB שבו
    הם מרווח של בדיקה 2.7 על הפריסה **המכווצת**, לא דרישה (נמדד 04/11 על
    ה-testrunner: ההנחה הזו הייתה גבוהה ב-245,248 בייט, בדיוק 262144-16896)."""
    m = c_d_e()
    smallest = minfree.min_disk_bytes(m, gb)
    end_full = (C_START + (C_NOW + REC_SIZE + D_ORIG + E_ORIG) // 512) * 512
    assert smallest is not None
    if gb == 0:
        assert smallest == end_full + 33 * 512, smallest - end_full
    else:
        assert smallest == required_bytes(m) - 262144 + (D_ORIG - D_NOW) + (E_ORIG - E_NOW) \
            + 33 * 512 + (40 - 30) * GIB, "\u200fC: צריכה 40GiB (\u200f20 תפוסים + 20 פנויים) במקום 30"
    rc, err = _disk_fits(tmp_path / "at", m, smallest, gb)
    assert rc == "rc=0", err
    rc, err = _disk_fits(tmp_path / "below", m, smallest - 512, gb)
    assert rc == "rc=1", "סקטור אחד פחות — הסוכן חייב לסרב"
    assert minfree.refusal(m, smallest, gb) is None


def test_the_server_does_not_offer_the_image_below_d_and_e_at_their_source_size():
    m = c_d_e()
    floor = minfree.min_disk_bytes(m, 0)
    assert floor > DISK_128 * 512 > required_bytes(m), "עד #1897 (04/11) הוצע מ-required_bytes"
    assert minfree.min_disk_bytes(m, 0) <= DISK_160 * 512
    assert minfree.min_disk_bytes(m, 20) > DISK_160 * 512, "\u200fC: עם 13GB פנויים אינה 20"
