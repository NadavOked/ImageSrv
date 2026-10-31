"""#1354 — "fits" also means room to work: at least `min_free_gb` (a console
setting, default 20) left free in the system partition after the restore.

The owner's rule (13/10): a shrunk image restored to a small disk can "fit"
and leave Windows a few GB -- that is not a fit. The agent enforces it in the
one place that already says "does not fit" before a byte is written
(``disk_fits``, check 2.7), which every restore path calls: a cloning-room
round (``run_restore_drawers``), the build machine's restore (``run_restore``)
and the local clone (``lc_prepare_target``, and the screen's verdict
``lc_verdict_for``). The server shows the same rule: "fits a disk from X GB"
includes it, and the room refuses an image before a wave opens.

Principle 5 twice: ``used_bytes`` missing is "not checked", never "fits"; a
value that did not arrive from the server is the default 20, never 0 (0 is
"the rule is off", chosen only in the console).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from server.library.images import required_bytes

from test_agent import AGENT, BASH, posix, sh
from agentlib import flat_lib, lib_path

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GIB = 1 << 30
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
MSR = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
LNX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"


def win11(size: int = 60 * GIB, used: int | None = 52 * GIB) -> dict:
    """ESP, MSR, C: and the Windows 11 recovery after it -- a shrunk image:
    C: of `size` with `used` in it, i.e. `size - used` free as captured."""
    c_start = 239616
    rec_start = c_start + size // 512
    parts = [
        (1, ESP, "esp", "vfat", 2048, 100 << 20, 30 << 20),
        (2, MSR, "msr", "unknown", 206848, 16 << 20, 16 << 20),
        (3, WIN, "windows", "ntfs", c_start, size, used),
        (4, REC, "recovery", "ntfs", rec_start, 800 << 20, 500 << 20),
    ]
    m = {
        "schema": 1, "id": "img_1354", "name": "t", "family": 256,
        "scheme": "gpt", "sector_size": 512, "disk_guid": None,
        "source_disk_bytes": None, "min_target_bytes": None,
        "partitions": [
            {"index": i, "type_guid": g, "role": r, "fs": fs, "start_sector": st,
             "size_bytes": sz, "used_bytes": u, "file": f"p{i}.x.pcl.zst",
             "sha256": "aa" * 32, "expandable": False}
            for i, g, r, fs, st, sz, u in parts
        ],
    }
    m["min_target_bytes"] = required_bytes(m)   # the library refuses a manifest without it
    return m


def linux_root(size: int = 30 * GIB, used: int = 25 * GIB) -> dict:
    m = win11()
    m["partitions"] = [m["partitions"][0], dict(
        m["partitions"][2], type_guid=LNX, role="linux", fs="ext4",
        size_bytes=size, used_bytes=used, start_sector=206848)]
    return m


NEED = required_bytes(win11())


def box(tmp_path: Path, image: dict, disk: int, gb: str | None = None):
    """A drawer `sda` of `disk` bytes, the manifest, and the agent's own
    diskfit.sh with expand.sh (the candidate) -- no stubbed jq: the real rule."""
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "blockdev").write_text(f"#!/bin/sh\necho {disk}\n", encoding="utf-8")
    if gb is not None:
        (run / "min_free_gb").write_text(gb + "\n", encoding="utf-8")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(image), encoding="utf-8")
    prelude = (
        f"chmod 0755 {posix(stubs)}/blockdev; "
        f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
        f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
        + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in
                  ("common", "jsonq", "progress", "restore", "expand", "diskfit"))
        + "raid_member_reason() { :; }; "   # #1808: the RAID gate has its own tests (test_capture_gates.py); these disks are files
    )
    return run, path, prelude


def fits(tmp_path: Path, image: dict, disk: int, gb: str | None = "20", env: str = ""):
    run, path, prelude = box(tmp_path, image, disk, gb)
    out = sh(prelude + f'{env}disk_fits sda {posix(path)!r}; echo "rc=$?"')
    err = run / "targets" / "sda" / "error"
    return (out.strip().splitlines()[-1], err.read_text(encoding="utf-8").strip() if err.exists() else "",
            (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else "")


# --- the agent: disk_fits, the gate every restore path calls ----------------


@needs_shell
def test_a_shrunk_image_on_its_own_size_leaves_too_little_and_says_how_much(tmp_path):
    """8 GiB free as captured, the disk exactly the layout: nothing grows."""
    rc, err, _ = fits(tmp_path, win11(), NEED)
    assert rc == "rc=1"
    assert err == "לא נכנס — יישארו רק 8.0 GB פנויים ב-C:, המינימום 20GB"


@needs_shell
def test_a_disk_that_grows_c_to_exactly_the_minimum_fits(tmp_path):
    rc, err, _ = fits(tmp_path, win11(), NEED + 12 * GIB)
    assert (rc, err) == ("rc=0", "")


@needs_shell
def test_one_byte_less_than_the_minimum_does_not_fit(tmp_path):
    rc, err, _ = fits(tmp_path, win11(), NEED + 12 * GIB - 1)
    assert rc == "rc=1" and err.startswith("לא נכנס — יישארו רק "), err


@needs_shell
def test_the_minimum_is_the_console_setting_not_a_constant(tmp_path):
    """The same disk that fits under 20 does not under 40 -- no new version."""
    rc, err, _ = fits(tmp_path, win11(), NEED + 12 * GIB, gb="40")
    assert rc == "rc=1"
    assert err == "לא נכנס — יישארו רק 20.0 GB פנויים ב-C:, המינימום 40GB"


@needs_shell
def test_zero_in_the_console_turns_the_rule_off(tmp_path):
    assert fits(tmp_path, win11(), NEED, gb="0")[:2] == ("rc=0", "")


@pytest.mark.parametrize("gb", [None, "abc", "020", "501", "-1", ""])
@needs_shell
def test_a_value_that_did_not_arrive_or_is_bad_is_the_default_and_logged(tmp_path, gb):
    """Principle 5: not 0 (the rule off), and the log says why."""
    rc, err, log = fits(tmp_path, win11(), NEED + 12 * GIB - 1, gb=gb)
    assert rc == "rc=1" and "המינימום 20GB" in err, err
    assert "using the default 20GB" in log


@needs_shell
def test_an_unmeasured_system_partition_is_not_checked_and_not_written(tmp_path):
    rc, err, _ = fits(tmp_path, win11(used=None), 500 * 10**9)
    assert rc == "rc=1"
    assert err == "לא נבדק אם יישארו 20GB פנויים ב-C: — לא נמדד כמה תפוס בה (used_bytes חסר)"


@needs_shell
def test_no_expansion_chosen_means_c_does_not_grow(tmp_path):
    """EXPAND_OVERRIDE=none (#59): the round restores at the image's size --
    a big disk does not give C: room it will never get."""
    rc, err, _ = fits(tmp_path, win11(), 500 * 10**9, env="EXPAND_OVERRIDE=none ")
    assert rc == "rc=1" and "8.0 GB" in err, err


@needs_shell
def test_a_linux_image_names_the_linux_root(tmp_path):
    image = linux_root()
    rc, err, _ = fits(tmp_path, image, required_bytes(image))
    assert err == "לא נכנס — יישארו רק 5.0 GB פנויים במחיצת הלינוקס הראשית, המינימום 20GB"


@needs_shell
def test_an_image_without_a_system_partition_is_not_judged_by_it(tmp_path):
    image = win11()
    image["partitions"] = image["partitions"][:2]
    assert fits(tmp_path, image, required_bytes(image))[:2] == ("rc=0", "")


@needs_shell
def test_a_too_small_disk_is_still_named_by_its_shortfall_first(tmp_path):
    rc, err, _ = fits(tmp_path, win11(), NEED - 1)
    assert err.startswith("הדיסק קטן מדי ב-"), err


# --- the build machine's restore: refused before the table is touched -------


@needs_shell
def test_run_restore_refuses_before_sgdisk_runs(tmp_path):
    """The build machine restores through run_restore: the refusal lands on the
    target and the run, and the partition table was never written."""
    run, path, prelude = box(tmp_path, win11(), NEED, "20")
    stubs = tmp_path / "stubs"
    (stubs / "sgdisk").write_text(f"#!/bin/sh\necho \"$*\" >> {posix(tmp_path)}/sgdisk.calls\n",
                                  encoding="utf-8")
    out = sh(prelude + f"chmod 0755 {posix(stubs)}/sgdisk; "
             f'run_restore unicast sda http://s img {posix(path)!r}; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert (run / "state").read_text().strip() == "failed"
    assert "יישארו רק 8.0 GB" in (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert not (tmp_path / "sgdisk.calls").exists(), "the table was touched"


def test_every_restore_path_goes_through_disk_fits():
    """Round (drawers, through #1366's write_check_round), build machine /
    single restore, local clone."""
    for lib, call in (("drawers.sh", 'write_check_round "$_manifest" $_disks'),
                      ("writecheck.sh", 'disk_fits "$_wr_d" "$_wr_m" && '),   # #1808: raid_wipe, then write_check
                      ("restore.sh", 'disk_fits "$2" "$5"'),
                      ("lccopy.sh", 'disk_fits "$1" "$2"')):
        assert call in (lib_path(lib)).read_text(encoding="utf-8"), lib


# --- the value from the server ----------------------------------------------


def take(tmp_path: Path, answer: dict, before: str | None = None) -> tuple[str, str]:
    run = tmp_path / "run"
    run.mkdir()
    if before is not None:
        (run / "min_free_gb").write_text(before + "\n")
    (tmp_path / "resp.json").write_text(json.dumps(answer), encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in ("common", "jsonq", "diskfit"))
             + f'min_free_take {posix(tmp_path / "resp.json")!r}; min_free_gb')
    log = (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
    return out.strip().splitlines()[-1], log


@needs_shell
def test_the_hello_value_is_kept_for_the_boot(tmp_path):
    assert take(tmp_path, {"min_free_gb": 40})[0] == "40"


@needs_shell
def test_an_older_server_without_the_field_keeps_the_last_value(tmp_path):
    """The local clone uses the last value that arrived; none at all = 20."""
    assert take(tmp_path, {"known": True}, before="35")[0] == "35"


@pytest.mark.parametrize("bad", ["040", 501, -1, "x", 1.5, True])
@needs_shell
def test_a_bad_value_from_the_server_is_the_default_and_logged(tmp_path, bad):
    value, log = take(tmp_path, {"min_free_gb": bad}, before="35")
    assert value == "20"
    assert "min_free_gb from the server is not 0-500" in log


# --- the local clone's verdict (the screen, before anything is written) -----


def lc_box(tmp_path: Path, used_c: str, target: int, gb: str = "20", shrink: bool = False,
           roles: dict | None = None, oem: bool = False) -> str:
    """inv/ files exactly as lc_inventory writes them: src = the win11 layout,
    one target of `target` bytes; measure/src done (shrink) or none. `roles`
    = inv/src.roles (the System32 check, #1369); `oem` gives partition 2 the
    Basic Data GUID -- an OEM/data partition in front of C:."""
    lc = tmp_path / "run" / "lc"
    (lc / "inv").mkdir(parents=True)
    (lc / "measure").mkdir()
    (tmp_path / "run" / "min_free_gb").write_text(gb + "\n")
    m = win11()
    if oem:
        m["partitions"][1]["type_guid"] = WIN
    if roles is not None:
        (lc / "inv" / "src.roles").write_text("".join(f"{i}|{r}\n" for i, r in roles.items()))
    parts = "".join(f'{p["index"]}|{p["type_guid"]}|U{p["index"]}|{p["start_sector"]}|{p["size_bytes"] // 512}\n'
                    for p in m["partitions"])
    (lc / "inv" / "src.parts").write_text(parts)
    used = {1: str(30 << 20), 2: str(16 << 20), 3: used_c, 4: str(500 << 20)}
    (lc / "inv" / "src.used").write_text("".join(f"{i}|{u}\n" for i, u in used.items()))
    need = NEED
    (lc / "inv" / "src.line").write_text(f"1|src|M|S1|{need}|ok||Windows|4|0|{need}|1|\n")
    (lc / "inv" / "tgt.line").write_text(f"2|tgt|M|S2|{target}|ok||||0|-1|0|\n")
    if shrink:
        # C: compacted to 54 GiB (2 GiB margin): what shrinkplan would write.
        small = 54 * GIB // 512
        compact = parts.replace(f"|{60 * GIB // 512}\n", f"|{small}\n").replace(
            f"|{239616 + 60 * GIB // 512}|", f"|{239616 + small}|")
        (lc / "measure" / "src.compact").write_text(compact)
        need_min = (239616 + small) * 512 + (800 << 20) + 262144
        (lc / "measure" / "src").write_text(f"done|{need_min}|\n")
    else:
        (lc / "measure" / "src").write_text("none||\n")
    run = tmp_path / "run"
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log LC={posix(lc)!r}; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in
                       ("common", "jsonq", "progress", "restore", "expand", "diskfit", "manifest", "lcinv"))
             + "lc_verdict_for src tgt")
    return out.strip().splitlines()[-1]


@needs_shell
def test_local_clone_a_bigger_target_that_leaves_enough_still_fits(tmp_path):
    assert lc_box(tmp_path, str(52 * GIB), NEED + 12 * GIB).startswith("fits|")


@needs_shell
def test_local_clone_fits_by_size_but_not_by_free_space_is_short_with_the_number(tmp_path):
    v = lc_box(tmp_path, str(52 * GIB), NEED + 4 * GIB)
    assert v == f"short|{NEED}|לא נכנס — יישארו רק 12.0 GB פנויים ב-C:, המינימום 20GB", v


@needs_shell
def test_local_clone_with_a_shrink_is_judged_on_the_compacted_layout(tmp_path):
    """Smaller than the source, fits once the source is shrunk -- and C: on
    the target is the compacted C: grown to the target's end."""
    target = NEED - 3 * GIB
    v = lc_box(tmp_path, str(52 * GIB), target, shrink=True)
    assert v.startswith("short|") and "יישארו רק 5.0 GB" in v, v
    assert lc_box(tmp_path / "b", str(30 * GIB), target, shrink=True).startswith("shrink|")


@needs_shell
def test_local_clone_unmeasured_c_is_unchecked_not_fits(tmp_path):
    v = lc_box(tmp_path, "", NEED + 100 * GIB)
    assert v.startswith("unchecked||לא נבדק אם יישארו 20GB"), v


@needs_shell
def test_local_clone_judges_the_system32_partition_not_the_first_basic_data_one(tmp_path):
    """#1369: the Basic Data GUID is shared by C: and any OEM/data partition.
    The inventory checks for Windows/System32 (inv/<src>.roles) and the rule
    follows it. By the GUID alone both are windows, and since #1875 the larger
    one is C: (until then the 16 MiB OEM partition in front was, "0.0 GB")."""
    roles = {1: "esp", 2: "data", 3: "windows", 4: "recovery"}
    assert lc_box(tmp_path / "a", str(52 * GIB), NEED + 12 * GIB, roles=roles, oem=True).startswith("fits|")
    by_guid = lc_box(tmp_path / "b", str(52 * GIB), NEED + 12 * GIB, oem=True)
    assert by_guid.startswith("fits|"), by_guid


@needs_shell
def test_local_clone_a_system_check_that_failed_is_unchecked(tmp_path):
    roles = {1: "esp", 2: "msr", 3: "unchecked", 4: "recovery"}
    v = lc_box(tmp_path, str(52 * GIB), NEED + 100 * GIB, roles=roles)
    assert v == "unchecked||מחיצה 3: לא הצלחנו לבדוק אם היא מחיצת המערכת", v


def test_the_local_clone_screen_shows_the_reason_of_a_short_verdict():
    src = (Path(__file__).resolve().parent.parent / "native-gui" / "src" / "screens_local.c").read_text(encoding="utf-8")
    block = src[src.index("case LCV_SHORT:"):src.index("default:", src.index("case LCV_SHORT:"))]
    assert 'if (f->reason[0]) { snprintf(out, n, "%s. לא ישמש כיעד.", why); return; }' in block   # #1937: why = the reason, no trailing "."


# --- one rule, two implementations ------------------------------------------


def test_the_default_and_the_range_are_one_number_in_agent_and_server():
    from server.library import minfree   # lazy: the negative control removes the module
    text = (AGENT / "lib" / "restore" / "diskfit.sh").read_text(encoding="utf-8")
    assert re.search(r"^MIN_FREE_GB_DEFAULT=(\d+)$", text, re.M).group(1) == str(minfree.MIN_FREE_GB_DEFAULT)
    assert re.search(r"^MIN_FREE_GB_MAX=(\d+)$", text, re.M).group(1) == str(minfree.MIN_FREE_GB_MAX)


CASES = [
    (win11(), NEED), (win11(), NEED + 12 * GIB - 1), (win11(), NEED + 12 * GIB),
    (win11(), NEED + GIB // 2), (win11(used=None), NEED), (linux_root(), required_bytes(linux_root())),
    (win11(size=200 * GIB, used=50 * GIB), required_bytes(win11(size=200 * GIB, used=50 * GIB))),
]


@pytest.mark.parametrize("image,disk", CASES)
@needs_shell
def test_the_server_says_exactly_what_the_agent_says(tmp_path, image, disk):
    from server.library import minfree   # lazy: the negative control removes the module
    _rc, err, _ = fits(tmp_path, image, disk)
    assert err == (minfree.refusal(image, disk, 20) or "")


@needs_shell
def test_the_smallest_disk_the_console_shows_is_the_agents_boundary(tmp_path):
    """"Fits a disk from X" = the agent passes at X and refuses at X-1."""
    from server.library import minfree   # lazy: the negative control removes the module
    smallest = minfree.min_disk_bytes(win11(), 20)
    assert smallest == NEED + 12 * GIB
    assert fits(tmp_path / "a", win11(), smallest)[0] == "rc=0"
    assert fits(tmp_path / "b", win11(), smallest - 1)[0] == "rc=1"


def test_min_disk_is_the_layout_when_there_is_room_already_or_the_rule_is_off():
    from server.library import minfree   # lazy: the negative control removes the module
    roomy = win11(size=200 * GIB, used=50 * GIB)
    assert minfree.min_disk_bytes(roomy, 20) == required_bytes(roomy)
    assert minfree.min_disk_bytes(win11(), 0) == NEED


def test_min_disk_is_unknown_when_c_was_not_measured():
    from server.library import minfree   # lazy: the negative control removes the module
    assert minfree.min_disk_bytes(win11(used=None), 20) is None


def test_min_disk_never_lands_inside_expand_lasts_threshold():
    """Short by half a GiB: expand_last does not stretch by less than 1 GiB,
    so the smallest disk is past that threshold, not at +0.5 GiB."""
    from server.library import minfree   # lazy: the negative control removes the module
    image = win11(size=60 * GIB, used=40 * GIB + GIB // 2)
    assert minfree.min_disk_bytes(image, 20) == required_bytes(image) + GIB + 1


@pytest.mark.parametrize("raw,want", [("20", 20), (0, 0), ("0", 0), (500, 500), ("500", 500),
                                      ("501", None), (-1, None), ("020", None), ("x", None),
                                      (True, None), (1.5, None), ("", None), (None, None)])
def test_parse_gb(raw, want):
    from server.library import minfree   # lazy: the negative control removes the module
    assert minfree.parse_gb(raw) == want
