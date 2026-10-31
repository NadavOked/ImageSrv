"""‏#1898 — הכרעת נדב (22/10): הכלל של #1897 חל גם כש-C: יושבת **לפני** הלינוקס.

dual-boot שנקלט עם כיווץ: ‏C: מכווצת (‏60→20GiB), ואחריה שורש הלינוקס — המועמד
להרחבה, כי הוא ה-windows/linux האחרון על הדיסק. עד כאן כל המקום הנוסף הלך
ללינוקס, ‏C: נשארה עם ~5GiB פנויים לתמיד, ו-`min_disk_bytes` החזיר `None`: האימג'
לא הוצע לשום דיסק, בלי אזהרה.

עכשיו C: מקבלת את חלקה לפי היחס במקור **במקומה**, והלינוקס (וכל מה שבין
השתיים) זז קדימה — בטוח, כי הטבלה נכתבת לפני הבייט הראשון והזרם כותב לפי
אינדקס. ‏min-free רואה את החלק של C: בשני התאומים. **והמשכפל שואל לפני**:
`share_plan` מחשב את החלוקה שתיכתב, והשאלה (`share`) מציגה אותה.
"""

from __future__ import annotations

import json
import shutil

import pytest

from server.library import minfree
from server.library.images import required_bytes

from test_agent import AGENT, BASH, posix, run_expand, sh, tail_n
from test_cloner_gui import sh as sh_run   # CompletedProcess, with a timeout
from agentlib import flat_lib

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GIB = 1 << 30
DISK = 976773168                  # ‏500GB, בסקטורים
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LIN = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
SWAP = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
C_START = 206848
L_START = C_START + 20 * GIB // 512
S_START = L_START + 30 * GIB // 512
FITS_EXACTLY = S_START + 2 * GIB // 512 + 2048   # the layout and a MiB: nothing to share


def dual_boot(shrunk: bool = True) -> dict:
    """ESP, ‏C: ‏60→20GiB (‏used 15), שורש לינוקס 30GiB שלא כווץ, swap 2GiB."""
    parts = [
        dict(index=1, type_guid=ESP, role="esp", fs="vfat", start_sector=2048,
             size_bytes=100 << 20, used_bytes=30 << 20),
        dict(index=2, type_guid=WIN, role="windows", fs="ntfs", start_sector=C_START,
             size_bytes=20 * GIB, used_bytes=15 * GIB, system=True),
        dict(index=3, type_guid=LIN, role="linux", fs="ext4", start_sector=L_START,
             size_bytes=30 * GIB, used_bytes=10 * GIB),
        dict(index=4, type_guid=SWAP, role="swap", fs="swap", start_sector=S_START,
             size_bytes=2 * GIB, used_bytes=None),
    ]
    if shrunk:
        parts[1]["shrunk_from_bytes"] = 60 * GIB
    for p in parts:
        p.setdefault("expandable", False)
        p.update(file=None if p["fs"] == "swap" else f"p{p['index']}.x.pcl.zst",
                 sha256=None if p["fs"] == "swap" else "aa" * 32, unique_guid="", uuid="", attrs="")
    m = {"schema": 1, "id": "img_1898", "name": "t", "family": 256, "scheme": "gpt",
         "sector_size": 512, "disk_guid": None, "source_disk_bytes": None, "partitions": parts}
    m["min_target_bytes"] = required_bytes(m)
    return m


def plan_of(m: dict) -> list[str]:
    keys = ("index", "type_guid", "role", "fs", "start_sector", "size_bytes", "file", "sha256")
    return ["|".join("" if p[k] is None else str(p[k]) for k in keys)
            + f"|{str(p['expandable']).lower()}|{p['unique_guid']}|{p['uuid']}|{p['attrs']}"
            for p in m["partitions"]]


def _room() -> int:
    table_end = L_START + 30 * GIB // 512
    return DISK - 34 - table_end - 2 * GIB // 512


@needs_shell
def test_c_before_the_root_grows_in_place_and_the_root_moves_forward(tmp_path):
    m = dual_boot()
    rc, calls, marker = run_expand(tmp_path, plan_of(m), disk_sectors=DISK, manifest=m, share_ok=True)
    assert rc == "rc=0", (rc, calls)
    assert marker == "3|ext4"
    g = _room() * 60 // 90 // 2048 * 2048       # C: 60 of (30 own + 60) GiB
    swap_n, _ = tail_n(4, 2 * GIB // 512)
    call = calls[0]
    assert f" {swap_n} " in f" {call} ", call
    c_end = C_START + 20 * GIB // 512 + g - 1
    assert (f" -d 3 -d 2 -n 2:{C_START}:{c_end} -t 2:{WIN} -n 3:{L_START + g}:0 -t 3:{LIN} "
            in f" {call} "), call
    more = (tmp_path / "box/run/targets/sda/expanded.more").read_text().split()
    assert more == ["2|ntfs"], "C: חייבת לגדול גם כמערכת קבצים אחרי הזרם"


@needs_shell
@pytest.mark.parametrize("shrunk,approved", [(False, True), (True, False)])
def test_without_a_shrunk_c_or_an_approval_the_root_takes_all_as_before(tmp_path, shrunk, approved):
    """לא כווצה — אין מה לחלק. כווצה ולא אושר (תחנה, שיכפול מקומי: לא נשאלו) —
    כמו קודם: נדב אמר "המשכפל שואל לפני", וחלוקה שאיש לא ראה אינה נכתבת."""
    m = dual_boot(shrunk=shrunk)
    rc, calls, _ = run_expand(tmp_path, plan_of(m), disk_sectors=DISK, manifest=m, share_ok=approved)
    assert rc == "rc=0"
    assert f" -d 3 -n 3:{L_START}:0 -t 3:{LIN}" in calls[0], calls[0]
    assert " -d 2" not in calls[0]


# --- min-free: C:'s share, in the agent and in the server -------------------------


def _agent_free(tmp_path, m: dict, disk: int, approved: bool = True) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path)!r}/log SHARE_HEAD={'1' if approved else ''}; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in
                       ("common", "jsonq", "progress", "restore", "expand", "diskfit"))
             + f'system_free_after {posix(path)!r} {disk} {required_bytes(m)} 3')
    return out.strip().splitlines()[-1]


@needs_shell
@pytest.mark.parametrize("disk", [DISK * 512, 256060514304, 128 * GIB])
def test_the_agent_and_the_server_give_c_before_the_root_the_same_share(tmp_path, disk):
    m = dual_boot()
    free, role = minfree.free_after(m, disk)
    grow = disk - required_bytes(m)
    expect = 5 * GIB + (int(grow * (60 / 90)) if grow > minfree.EXPAND_THRESHOLD else 0)
    assert role == "windows" and abs(free - expect) <= 1, (free, expect)
    assert _agent_free(tmp_path / str(disk), m, disk) == f"{free}|windows"


@needs_shell
def test_without_the_approval_the_agent_counts_no_growth_for_c(tmp_path):
    """the same answer as expand_last without share.ok: C: stays as captured."""
    m = dual_boot()
    assert _agent_free(tmp_path, m, DISK * 512, approved=False) == f"{5 * GIB}|windows"


def test_the_image_is_offered_from_the_disk_where_c_gets_enough():
    """‏C: צריכה עוד 15GiB; היא מקבלת 60/90 מהמקום — 22.5GiB מעל הפריסה."""
    m = dual_boot()
    need = required_bytes(m)
    smallest = minfree.min_disk_bytes(m, 20)
    assert smallest is not None, "‏#1898: האימג' לא הוצע לשום דיסק"
    assert abs(smallest - (need + -(-15 * GIB * 90 // 60))) <= 2
    assert minfree.refusal(m, smallest, 20) is None
    assert minfree.refusal(m, smallest - 1, 20) is not None


# --- the question: share_plan says what expand_last will write ----------------------

LIBS = ("common", "waits", "jsonq", "sysinfo", "progress", "restore", "expand", "grow", "ntfsunhib",
        "expandshare", "writemark", "diskfit", "askgui", "smartgate", "drawergate")


def _plan(tmp_path, m: dict, disk_sectors: int) -> list[str]:
    run = tmp_path / "run"; (run / "targets/sda").mkdir(parents=True)
    (tmp_path / "sys/block/sda").mkdir(parents=True)
    (tmp_path / "sys/block/sda/size").write_text(f"{disk_sectors}\n", newline="\n")
    (tmp_path / "m.json").write_text(json.dumps(m), encoding="utf-8")
    out = sh_run(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(tmp_path)!r} LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + f'share_plan sda {posix(tmp_path / "m.json")!r}; echo "rc=$?"')
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1] == "rc=0", out.stdout + out.stderr
    return lines[:-1]


@needs_shell
def test_the_plan_shown_is_the_split_expand_writes(tmp_path):
    m = dual_boot()
    rows = _plan(tmp_path, m, DISK)
    g = _room() * 60 // 90 // 2048 * 2048
    assert rows[0] == f"2|{20 * GIB}|{20 * GIB + g * 512}", rows
    idx, now, after = rows[-1].split("|")
    assert (idx, int(now)) == ("3", 30 * GIB)
    # the root: from its new start to the swap at the end
    assert abs(int(after) - ((DISK - 34 - (L_START + g)) * 512 - 2 * GIB)) < 2 * (1 << 20), rows
    assert not (tmp_path / "run/targets/sda/expanded.more").exists(), "a plan writes no marks"


@needs_shell
def test_no_question_when_nothing_before_the_candidate_grows(tmp_path):
    assert _plan(tmp_path / "a", dual_boot(shrunk=False), DISK) == []
    assert _plan(tmp_path / "b", dual_boot(), FITS_EXACTLY) == []   # no room to share


def _gate(tmp_path, answer: str | None):
    run = tmp_path / "run"; gui = run / "gui"; gui.mkdir(parents=True)
    for d in ("sda", "sdb"):
        (run / "targets" / d).mkdir(parents=True)
        (run / "targets" / d / "state").write_text("waiting\n")
        (tmp_path / "sys/block" / d).mkdir(parents=True)
    (tmp_path / "sys/block/sda/size").write_text(f"{DISK}\n")
    (tmp_path / "sys/block/sdb/size").write_text(f"{FITS_EXACTLY}\n")   # no room: not asked
    (run / "manifest.json").write_text(json.dumps(dual_boot()), encoding="utf-8")
    reply = ("( for _ in $(seq 1 100); do [ -s \"$GUI_DIR/ask-request\" ] && break; sleep 0.1; done; "
             "cp \"$GUI_DIR/ask-request\" \"$RUN_DIR/request.seen\"; "
             "N=$(IFS='|' read -r n _ < \"$GUI_DIR/ask-request\"; echo \"$n\"); "
             f"printf '%s|{answer}\\n' \"$N\" > \"$GUI_DIR/ask-decision\" ) & " if answer else "")
    out = sh_run(f"export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} SYSROOT={posix(tmp_path)!r} "
             f"LOG_FILE={posix(run)!r}/log IMAGESRV_TEST=1; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + "attended() { shift; \"$@\"; }; attended_power_check() { :; }; "
             + 'disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; esac; }; '
             + "sleep 30 & _gui_pid=$!; " + reply
             + 'WAIT_SMART_GUI_S=4 drawer_share_gate "$RUN_DIR/manifest.json" sda sdb > "$RUN_DIR/out"; '
             'kill $_gui_pid; wait', timeout=60)
    return out, run


@needs_shell
def test_the_cloner_asks_and_writes_only_on_split(tmp_path):
    out, run = _gate(tmp_path / "yes", "split")
    assert (run / "out").read_text().split() == ["sdb", "sda"], out.stdout + out.stderr
    seen = (run / "request.seen").read_text().splitlines()
    assert seen[0] == "1-0|share|2|sda" and all(r.startswith("1|") for r in seen[1:]), seen
    assert (run / "targets/sda/state").read_text().strip() == "waiting"
    assert (run / "targets/sda/share.ok").exists(), "expand_last applies the split only with it"
    assert not (run / "targets/sdb/share.ok").exists()

    out, run = _gate(tmp_path / "no", None)
    assert (run / "out").read_text().split() == ["sdb"], out.stdout + out.stderr
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert "חלוקת המקום" in (run / "targets/sda/error").read_text()
    assert not (run / "targets/sda/share.ok").exists()


from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402
from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token  # noqa: E402


@GUI_TOOLS
def test_the_split_question_is_drawn_over_the_cloner(tmp_path):
    binary = _build_gui(tmp_path)
    state = BASE_STATE + ("ask_prompt=7-0|share|2|sda\nask_row=1|2|21474836480|166429982720\n"
                          "ask_row=1|3|32212254720|104152760320\n")
    _render(binary, tmp_path / "s", state)
    _w, _h, ch, plain = _png_rgb(tmp_path / "s" / "card-cloner-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "s" / "card-cloner-share-light.png")
    warn = _token("--clr-warning")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    run = _render(binary, tmp_path / "bad", BASE_STATE + "ask_prompt=7-0|share|1|sda\nask_row=1|2|200|100\n")
    assert "question incomplete or malformed" in run.stderr


@needs_shell
@pytest.mark.parametrize("approved", [True, False])
def test_disk_fits_counts_c_s_share_only_when_the_split_was_approved(tmp_path, approved):
    from test_agent import _stub
    m = dual_boot()
    run = tmp_path / "run"; (run / "targets/sda").mkdir(parents=True)
    (run / "targets/sda/state").write_text("writing\n")
    if approved:
        (run / "targets/sda/share.ok").write_text("")
    (run / "min_free_gb").write_text("20\n")
    (tmp_path / "m.json").write_text(json.dumps(m), encoding="utf-8")
    stubs = tmp_path / "stubs"; stubs.mkdir()
    out = sh(_stub(stubs / "blockdev", f"echo {DISK * 512}")
             + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in
                       ("common", "jsonq", "progress", "restore", "expand", "diskfit"))
             + "raid_member_reason() { :; }; "
             + f'disk_fits sda {posix(tmp_path / "m.json")!r}; echo "rc=$?"')
    err = run / "targets/sda/error"
    if approved:
        assert out.strip().endswith("rc=0"), err.read_text() if err.exists() else out
    else:
        assert out.strip().endswith("rc=1")
        assert "יישארו רק 5.0 GB פנויים ב-C:" in err.read_text(encoding="utf-8")
