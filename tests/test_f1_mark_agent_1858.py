"""‏#1858 (מוקאפ 07/11, נדב) — הסוכן: "נצפה F1 בהדלקה" לפי מספר סידורי.

ה-hello עונה `f1_observed` (‏[{serial, date, source}]). `f1mark.sh` הופך את הדיסק המסומן לאדום — אותו אדום של
קריאה-בלבד (`smart_ro_check`), סיבה `f1_observed` — ולכן הוא מדולג בשער (#1732), נראה אדום בהמתנה, ואומר
"נצפה F1" ולא "SMART תקול". ה-SMART האמיתי נשמר מתחתיו (`.under`), והמסך אומר מה הוא.

הכללים: הזהות היא ה-serial (לא שם ההתקן ולא החריץ); שדה חסר/פגום ב-hello אינו "אין סימונים"; ניקוי מחזיר
את הדיסק לשיפוט ה-SMART שלו; דיסק שה-SMART שלו אדום ממילא נשאר באדום שלו. בלי סימון — הכול כמו היום.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_cloner_gui import env as gui_env
from test_smart import AGENT, BASH, HEALTH_FAIL, HEALTH_OK, PRELUDE, env, full, make_smartctl, posix, preflight, sh
from test_smart_tables_1921 import probe

pytestmark = requires_native(("bash", BASH), "jq", why="the F1 marks come from the hello reply, read with jq")

F1 = f'. {posix(AGENT)}/lib/smart/f1mark.sh; '
SERIAL = "SER-870"
#: the gate harness (test_smart.fake_memory) names every serial S-<dev> and stubs json_get_join: the marks file is written by hand
STUB_REFRESH = 'disk_f1_refresh() { :; }; '


def _sysro(tmp_path: Path, dev: str = "sda", serial: str = SERIAL) -> None:
    dd = tmp_path / "sysro" / "sys" / "block" / dev / "device"
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "serial").write_text(serial + "\n", newline="\n")


def _marks(*serials: str) -> str:
    """The shell that writes the marks file the way `disk_f1_refresh` would."""
    return 'mkdir -p "$RUN_DIR"; : > "$RUN_DIR/disk_f1"; ' + "".join(
        f'echo "{s}|2026-11-05|manual" >> "$RUN_DIR/disk_f1"; ' for s in serials)


# ---------- מהתשובה לקובץ ----------

def _refresh(tmp_path: Path, reply: str | None, before: str = "") -> tuple[int, str]:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    if reply is not None:
        (run / "response.json").write_text(reply, newline="\n")
    if before:
        (run / "disk_f1").write_text(before, newline="\n")
    out = sh(env(run, tmp_path / "dev") + PRELUDE + F1 + 'disk_f1_refresh; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    got = (run / "disk_f1").read_text(encoding="utf-8") if (run / "disk_f1").exists() else ""
    return int(out.stdout.strip().rsplit("rc=", 1)[1]), got


def test_the_reply_becomes_one_line_per_marked_disk(tmp_path):
    reply = json.dumps({"schema": 1, "f1_observed": [{"serial": "A1", "date": "2026-11-05", "source": "manual"},
                                                     {"serial": "B|2", "date": None, "source": "auto"}]})
    rc, got = _refresh(tmp_path, reply)
    assert rc == 0 and got.splitlines() == ["ok", "A1|2026-11-05|manual", "B|2||auto"], got   # "ok" first: the field was read


def test_an_empty_list_clears_the_marks(tmp_path):
    rc, got = _refresh(tmp_path, '{"schema":1,"f1_observed":[]}', before="A1|2026-11-05|manual\n")
    assert rc == 0 and got == "ok" + chr(10), got   # read, and empty


@pytest.mark.parametrize("reply", ['{"schema":1}', "not json", '{"schema":1,"f1_observed":', ""])
def test_a_missing_or_broken_field_keeps_the_previous_marks(tmp_path, reply):
    """שרת ישן, קריאה שנכשלה, קובץ באמצע כתיבה: זה אינו "אין סימונים" (עיקרון 5)."""
    rc, got = _refresh(tmp_path, reply, before="A1|2026-11-05|manual\n")
    assert rc == 1 and got == "A1|2026-11-05|manual\n", (rc, got)


# ---------- הדיסק המסומן אדום, לפי serial ----------

def test_a_marked_disk_is_red_with_its_smart_kept_beneath(tmp_path):
    _sysro(tmp_path)
    verdict, record, run = probe(tmp_path, full(HEALTH_OK), extra=F1 + _marks(SERIAL))
    assert verdict == "fail" and record[:2] == ["fail", "f1_observed"], record
    assert (run / "smart" / "sda.under").read_text().strip() == f"{SERIAL}|ok"


def test_an_unmarked_disk_is_as_before(tmp_path):
    _sysro(tmp_path)
    verdict, record, run = probe(tmp_path, full(HEALTH_OK), extra=F1 + _marks("SOMEONE-ELSE"))
    assert verdict == "ok" and record[0] == "ok", record
    assert not (run / "smart" / "sda.under").exists()


def test_the_identity_is_the_serial_not_the_device_name(tmp_path):
    """sda עם serial אחר אינו מסומן, גם אם דיסק אחר שנקרא sda סומן קודם."""
    _sysro(tmp_path, serial="NEW-DISK-IN-THE-SAME-SLOT")
    verdict, _, _ = probe(tmp_path, full(HEALTH_OK), extra=F1 + _marks(SERIAL))
    assert verdict == "ok"


def test_a_disk_with_no_serial_is_never_marked(tmp_path):
    (tmp_path / "sysro" / "sys" / "block" / "sda" / "device").mkdir(parents=True)
    verdict, _, _ = probe(tmp_path, full(HEALTH_OK), extra=F1 + _marks(""))
    assert verdict == "ok"


def test_a_disk_red_for_its_own_smart_keeps_its_reason_and_records_it_beneath(tmp_path):
    _sysro(tmp_path)
    verdict, record, run = probe(tmp_path, full(HEALTH_FAIL), rc=8, extra=F1 + _marks(SERIAL))
    assert verdict == "fail" and record[:2] == ["fail", "health_failed"], record
    assert (run / "smart" / "sda.under").read_text().strip() == f"{SERIAL}|fail"


def test_clearing_the_mark_returns_the_disk_to_its_own_smart(tmp_path):
    _sysro(tmp_path)
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    (tmp_path / "sysro" / "sys" / "block" / "sda" / "ro").write_text("0\n", newline="\n")
    sc = make_smartctl(tmp_path, full(HEALTH_OK))
    out = sh(env(run, dev, sysroot=tmp_path / "sysro", smartctl=sc) + PRELUDE + F1 + _marks(SERIAL)
             + 'echo "marked=$(smart_hello_field sda) $(smart_field sda 2)"; '
             + _marks() + 'echo "cleared=$(smart_hello_field sda) $(smart_probe sda)"')
    assert out.returncode == 0, out.stderr
    # הסימון הוסר: הרשומה האדומה נמחקת (ה-hello אומר "לא נבדק" עד ה-probe הבא, לא "אדום"), וה-probe חוזר ל-SMART שלו
    assert "marked=fail f1_observed" in out.stdout and "cleared=unchecked ok" in out.stdout, out.stdout


def test_without_the_library_loaded_nothing_changes(tmp_path):
    """The call is guarded (`command -v`): a room that does not load f1mark.sh judges by SMART only."""
    _sysro(tmp_path)
    verdict, _, _ = probe(tmp_path, full(HEALTH_OK), extra=_marks(SERIAL))   # the file exists, the functions do not
    assert verdict == "ok"


# ---------- השער: מדולג ואומר למה ----------

def test_the_gate_skips_a_marked_disk_and_says_f1_not_smart(tmp_path):
    written, run, err = preflight(tmp_path, full(HEALTH_OK), extra=F1 + STUB_REFRESH + _marks("S-sda"))
    assert written == "", (written, err)
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    line = (run / "targets/sda/error").read_text(encoding="utf-8")
    assert "נצפה F1" in line and "SMART נכשל" not in line and "f1_observed" not in line, line


def test_the_gate_writes_the_unmarked_neighbour(tmp_path):
    written, run, err = preflight(tmp_path, full(HEALTH_OK), disks="sda sdb", extra=F1 + STUB_REFRESH + _marks("S-sda"))
    assert written == "sdb", (written, err)


# ---------- המסך הממתין: שלושה שדות חדשים ----------

def _idle(tmp_path: Path, marked: bool, smart_line: str = "ok ok 0 0 0 0") -> str:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    sysroot = tmp_path / "sysroot"
    reply = {"schema": 1, "f1_observed": [{"serial": "S-sdb", "date": "2026-11-05", "source": "auto"}] if marked else []}
    (run / "response.json").write_text(json.dumps(reply), newline="\n")
    (run / "smart").mkdir()
    for dev in ("sda", "sdb"):
        (sysroot / "sys" / "block" / dev / "device").mkdir(parents=True)
        (sysroot / "sys" / "block" / dev / "size").write_text("1000\n", newline="\n")
        (sysroot / "sys" / "block" / dev / "ro").write_text("0\n", newline="\n")
        (sysroot / "sys" / "block" / dev / "device" / "model").write_text(f"Model {dev}\n", newline="\n")
        (run / "smart" / f"{dev}.v").write_text(smart_line + "\n", newline="\n")
        (run / "smart" / f"{dev}.serial").write_text(f"S-{dev}\n", newline="\n")
    script = (gui_env(run, gui) + f'export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
              f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; disk_port() {{ case "$1" in sda) echo 1;; sdb) echo 2;; esac; }}; '
              'disk_serial() { printf "S-%s" "$1"; }; cloner_gui_state')
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    return "\n".join(l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("cdisk="))


def test_the_idle_screen_gets_the_mark_the_date_the_source_and_the_smart_beneath(tmp_path):
    lines = _idle(tmp_path, marked=True).splitlines()
    sdb = next(l for l in lines if "|sdb|" in l).split("|")
    assert sdb[4:6] == ["fail", "f1_observed"], sdb                       # אדום, וה"סיבה" אינה מילת SMART
    assert sdb[13:16] == ["2026-11-05", "auto", "passed"], sdb            # 14/15/16: תאריך, מקור, ה-SMART של הדיסק
    sda = next(l for l in lines if "|sda|" in l).split("|")
    assert sda[4] == "passed" and sda[13:16] == ["", "", ""], sda          # הלא-מסומן: כמו היום, שלושה שדות ריקים


def test_the_idle_screen_without_marks_is_as_today(tmp_path):
    for line in _idle(tmp_path, marked=False).splitlines():
        assert line.endswith("|") and line.split("|")[4] == "passed" and line.split("|")[13:16] == ["", "", ""], line


def test_a_line_that_is_not_a_mark_is_not_read_as_one(tmp_path):
    """The kiosk harness stubs json_get_join: a failure-memory line (serial||cable) landed in the marks file.
    Only manual/auto sources are marks; anything else names no disk."""
    _sysro(tmp_path)
    run = tmp_path / "run"; run.mkdir()
    (run / "disk_f1").write_text(f"ok\n{SERIAL}||cable\n{SERIAL}|2026-11-05|\n", newline="\n")
    out = sh(env(run, tmp_path / "dev", sysroot=tmp_path / "sysro") + PRELUDE + F1 + 'disk_f1_observed sda; echo "rc=$?"')
    assert out.returncode == 0 and out.stdout.strip() == "rc=1", (out.stdout, out.stderr)
    (run / "disk_f1").write_text(f"ok\n{SERIAL}|2026-11-05|auto\n", newline="\n")
    out = sh(env(run, tmp_path / "dev", sysroot=tmp_path / "sysro") + PRELUDE + F1 + 'disk_f1_observed sda; echo "rc=$?"')
    assert out.stdout.split() == ["2026-11-05|auto", "rc=0"], out.stdout
