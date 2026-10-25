"""שערי המקור של הקליטה — לפני הבייט הראשון (#1901, ‏#1808, ‏#1900).

‏#1901: משימת קליטה נקשרה לשם ההתקן (`sda`) בלבד. סדר הדיסקים מתחלף בין
אתחולים, וה-sha256 מאמת את מה שנקרא — לא את הדיסק שהמפעיל בחר — ולכן אימג'
של הדיסק הלא נכון נכנס לספרייה בשם שניתן לו. מעכשיו ההזמנה נושאת את ה-serial
שה-hello דיווח, והסוכן מסרב כשה-serial של `/dev/<disk>` שונה.

‏#1808: חבר במערך Intel RST נראה ללינוקס כדיסק רגיל. הסירוב לפי חתימת
המטא-דאטה על הדיסק (העוגן של IMSM בסקטור הלפני-אחרון; ‏blkid לשאר), ו"לא
הצלחנו לבדוק" הוא סירוב — לא "אין RAID".

‏#1900: מערכת קבצים שהשרת לא יקבל, או מקור 4Kn, נקלטו במלואם ונדחו רק
ב-`finish_capture`. עכשיו — לפני בדיקת המקום ולפני הבייט הראשון.

כל הסירובים נמדדים במה שהקונסולה מקבלת (`targets[].error`), כמו #106.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import hello_body
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_capture_refusals import (EMPTY_TABLE, GPT_DISK, ONE_PARTITION, capture_run,
                                   refusal_reason)
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

#: ‏sgdisk שמשאיר סימן שנקרא — "הגענו לקריאת הטבלה" = עברנו את שערי המקור.
SGDISK_MARK = EMPTY_TABLE.replace("#!/bin/sh\n", '#!/bin/sh\ntouch "$RUN_DIR/sgdisk.ran"\n', 1)
NO_PARTITIONS = "לא נמצאו מחיצות"

ISW_SIGNATURE = b"Intel Raid ISM Cfg Sig. 1.3.00"


def _serial_pre(serial: str | None, extra_disks: tuple[str, ...] = ()) -> str:
    """ה-serial של sda כפי ש-sysfs מציג אותו (NVMe), ועוד דיסקים במחשב."""
    pre = ""
    if serial is not None:
        pre += ('mkdir -p "$SYSROOT/sys/block/sda/device"; '
                f'printf "%s\\n" {serial!r} > "$SYSROOT/sys/block/sda/device/serial"; ')
    for name in extra_disks:
        pre += f'mkdir -p "$SYSROOT/sys/block/{name}"; '
    return pre


# --- #1901: הדיסק שנבחר, לא שמו -----------------------------------------------


def test_a_different_serial_on_the_named_device_is_refused_before_the_table_is_read(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
                                shell_pre=_serial_pre("SERIAL-B"), serial="SERIAL-A")
    reason = refusal_reason(box, run, out)
    assert "אינו הדיסק שנבחר" in reason, reason
    assert "SERIAL-A" in reason and "SERIAL-B" in reason, reason
    assert not (run / "sgdisk.ran").exists(), "הטבלה נקראה — הסירוב לא היה לפני הקריאה"


def test_the_serial_it_was_ordered_for_passes_the_gate(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
                                shell_pre=_serial_pre("SERIAL-A"), serial="SERIAL-A")
    assert NO_PARTITIONS in refusal_reason(box, run, out)
    assert (run / "sgdisk.ran").exists()


def test_no_serial_at_ordering_on_the_only_disk_is_still_captured(tmp_path):
    """הגדרת ה"גמור" 2: מכונה עם דיסק יחיד בלי serial — נקלטת."""
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK}, serial="null")
    assert NO_PARTITIONS in refusal_reason(box, run, out)


def test_no_serial_at_ordering_with_two_disks_is_unknown_not_a_guess(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
                                shell_pre=_serial_pre(None, ("sdb",)), serial="null")
    reason = refusal_reason(box, run, out)
    assert "לא ניתן לוודא" in reason and "2 דיסקים" in reason, reason
    assert not (run / "sgdisk.ran").exists()


def test_the_task_carries_the_serial_the_console_saw_and_hello_hands_it_over(server):
    from test_capture import make_task, setup_build_machine
    mac = setup_build_machine(server)
    body = hello_body(mac)
    body["disks"] = [{**body["disks"][0], "dev": "sda", "serial": "WD-A"},
                     {**body["disks"][0], "dev": "sdb", "serial": "WD-B"}]
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    created = make_task(server, mac, disk="sdb").json()

    # ‏SELECT * ו-.get: בבקרה השלילית (בלי העמודה) הטסט נכשל על הערך, לא על SQL.
    row = dict(server["ctx"].conn.execute("SELECT * FROM tasks WHERE id = ?",
                                          (created["id"],)).fetchone())
    assert (row["disk"], row.get("disk_serial")) == ("sdb", "WD-B")
    task = server["anon"].post("/api/v1/agent/hello", json=body).json()["task"]
    assert (task["disk"], task.get("disk_serial")) == ("sdb", "WD-B")


def test_a_disk_the_machine_never_reported_is_ordered_with_an_unknown_serial(server):
    """לא דווח = לא ידוע (‏None), לא serial של דיסק אחר."""
    from test_capture import make_task, setup_build_machine
    mac = setup_build_machine(server)
    created = make_task(server, mac, disk="nvme0n1").json()
    row = dict(server["ctx"].conn.execute("SELECT * FROM tasks WHERE id = ?",
                                          (created["id"],)).fetchone())
    assert row.get("disk_serial") is None


# --- #1808: חבר במערך RAID ------------------------------------------------------


def _with_isw_anchor(disk: bytes = GPT_DISK, size: int = 16384) -> bytes:
    """העוגן של IMSM — חתימה בתחילת הסקטור הלפני-אחרון (כמו mdadm/libblkid)."""
    raw = bytearray(disk + bytes(size - len(disk)))
    raw[size - 1024:size - 1024 + len(ISW_SIGNATURE)] = ISW_SIGNATURE
    return bytes(raw)


def test_an_intel_rst_member_is_refused_by_its_signature(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, image=_with_isw_anchor(),
                                stubs={"sgdisk": SGDISK_MARK})
    reason = refusal_reason(box, run, out)
    assert "RAID של Intel" in reason and "AHCI" in reason, reason
    assert not (run / "sgdisk.ran").exists()


#: ‏blkid שמדווח חבר md על הדיסק השלם (‏-p), ושותק על מחיצות.
BLKID_MD_MEMBER = ('#!/bin/sh\ncase " $* " in *" -p "*) echo linux_raid_member; exit 0 ;; esac\n'
                   'exit 2\n')
#: ‏blkid שהבדיקה שלו עצמה נכשלה (‏rc 4) על הדיסק השלם.
BLKID_BROKEN_ON_DISK = '#!/bin/sh\ncase " $* " in *" -p "*) exit 4 ;; esac\nexit 2\n'


def test_a_member_blkid_names_is_refused_by_that_name(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK, "blkid": BLKID_MD_MEMBER})
    reason = refusal_reason(box, run, out)
    assert "linux_raid_member" in reason, reason


def test_a_raid_check_that_could_not_run_is_a_refusal_not_a_pass(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK, "blkid": BLKID_BROKEN_ON_DISK})
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לבדוק אם /dev/sda חבר במערך RAID" in reason and "rc=4" in reason, reason


def test_a_disk_tail_that_does_not_read_is_unchecked_not_none(tmp_path):
    """‏blkid עונה rc 2 גם על "לא נפתח" (נמדד, util-linux 2.41.5) — לכן הקריאה
    של סוף הדיסק היא הראיה. גודל ב-sysfs שגדול מהקובץ = קריאה קצרה."""
    box, run, out = capture_run(
        tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
        shell_pre='echo 999999 > "$SYSROOT/sys/block/sda/size"; ')
    reason = refusal_reason(box, run, out)
    assert "קריאת סוף הדיסק נכשלה" in reason, reason


def _raid_json(tmp_path: Path, image: bytes, role: str, stubs: dict | None = None) -> str:
    box = tmp_path / "rj"
    (box / "dev").mkdir(parents=True)
    (box / "run").mkdir()
    (box / "sys/block/sda").mkdir(parents=True)
    (box / "dev/sda").write_bytes(image)
    (box / "sys/block/sda/size").write_text(f"{len(image) // 512}\n", encoding="utf-8")
    stubs = dict(stubs or {})
    stubs.setdefault("blkid", "#!/bin/sh\nexit 2\n")
    return run_sh(
        make_stubs(box / "stubs", stubs)
        + f"export RUN_DIR={posix(box / 'run')!r} DEVROOT={posix(box / 'dev')!r} "
        f"SYSROOT={posix(box)!r} D_ROLE={role}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; "
        f". {posix(AGENT)}/lib/diskfit.sh; "
        'printf "[%s]" "$(raid_member_json sda)"').strip()


def test_hello_names_a_member_on_the_build_machine_in_three_states(tmp_path):
    assert _raid_json(tmp_path / "a", _with_isw_anchor(), "build") == '[,"raid_member":"isw_raid_member"]'
    assert _raid_json(tmp_path / "b", bytes(16384), "build") == '[,"raid_member":false]'
    assert _raid_json(tmp_path / "c", bytes(16384), "build",
                      {"blkid": BLKID_BROKEN_ON_DISK}) == '[,"raid_member":null]'
    assert _raid_json(tmp_path / "d", _with_isw_anchor(), "station") == "[]"


# --- #1900: מערכת קבצים לא נתמכת / ‏4Kn ----------------------------------------

#: ‏curl שרושם כל כתובת — "preflight לא נקרא" = הסירוב היה לפני בדיקת המקום.
CURL_LOG = ('#!/bin/sh\necho "$*" >> "$RUN_DIR/curl.log"\n'
            'case "$*" in */preflight*) echo \'{"status":"ok","reason":"space checked"}\'; exit 0 ;; esac\n'
            'exit 7\n')


def _blkid_fs(fs: str) -> str:
    """‏blkid: הדיסק השלם (‏-p) — כלום; מחיצה — `fs`."""
    return ('#!/bin/sh\ncase " $* " in *" -p "*) exit 2 ;; esac\n'
            f'case " $* " in *" TYPE "*) echo {fs}; exit 0 ;; esac\nexit 2\n')


@pytest.mark.parametrize("fs", ["xfs", "crypto_LUKS", "LVM2_member", "ext3", "ext2", "exfat", "ReFS"])
def test_an_unsupported_filesystem_is_refused_before_the_space_check(tmp_path, fs):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": ONE_PARTITION, "blkid": _blkid_fs(fs),
                                                 "curl": CURL_LOG})
    reason = refusal_reason(box, run, out)
    assert f"מערכת הקבצים {fs} אינה נתמכת" in reason, reason
    log = (run / "curl.log").read_text(encoding="utf-8") if (run / "curl.log").exists() else ""
    assert "preflight" not in log, f"בדיקת המקום רצה לפני הסירוב: {log}"


def test_a_partition_without_a_filesystem_is_still_read_raw(tmp_path):
    """אסור לסרב ל-unknown (‏MSR, ‏raw, ‏VeraCrypt הם dd תקין) — הקליטה ממשיכה
    לבדיקת המקום."""
    # בדיקת המקום עונה "refused" — הקליטה נעצרת שם ולא ממשיכה לזרם.
    curl = CURL_LOG.replace('"status":"ok","reason":"space checked"', '"status":"refused","reason":"STOP-AT-SPACE"')
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": ONE_PARTITION, "curl": curl})
    assert "preflight" in (run / "curl.log").read_text(encoding="utf-8")
    reason = refusal_reason(box, run, out)
    assert "STOP-AT-SPACE" in reason and "אינה נתמכת" not in reason, reason


def test_a_4kn_source_is_refused_by_name_before_the_table_is_read(tmp_path):
    box, run, out = capture_run(
        tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
        shell_pre='echo 4096 > "$SYSROOT/sys/block/sda/queue/logical_block_size"; ')
    reason = refusal_reason(box, run, out)
    assert "4Kn" in reason and "4096" in reason, reason
    assert not (run / "sgdisk.ran").exists()


def test_the_agent_and_the_server_accept_the_same_filesystems():
    """מקור אחד: הרשימה בסוכן היא התאום של `server/images.py:FILESYSTEMS`."""
    from server.images import FILESYSTEMS
    gate = AGENT / "lib/capturegate.sh"
    text = gate.read_text(encoding="utf-8") if gate.exists() else ""
    found = re.search(r'^CAPTURE_FILESYSTEMS="([^"]*)"', text, re.M)
    agent = set(found.group(1).split()) if found else set()
    assert agent == FILESYSTEMS - {""}
