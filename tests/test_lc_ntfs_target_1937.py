"""#1937 (the college, 21/10): "a disk refused as a target because of an inconsistent NTFS
on it". A target is wiped -- the state of its file system is nobody's business. What is
measured (ntfsresize --info, when a drawer is smaller than the layout) is the SOURCE, and a
source that cannot be measured is refused with the reason. That reason is drawn on every
smaller target's row (screens_local.c: "<reason>. לא ישמש כיעד."), so it must say it is the
source's -- and it ended in "." too, which made "..".
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))

REPO = Path(__file__).resolve().parent.parent

#: sda: one Windows partition of 400 GB (the source); sdb: 250 GB (smaller -- the measure runs)
SETUP = r'''
mkdir -p "$LC/inv" "$LC/measure"
printf '1|sda|A|s1|480000000000|ok|ok|Windows|1|1|400000000000|1|\n' > "$LC/inv/sda.line"
printf '2|sdb|B|s2|250000000000|ok|ok|Windows|1|1|240000000000|1|\n' > "$LC/inv/sdb.line"
printf '1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7||2048|781250000\n' > "$LC/inv/sda.parts"
printf '1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7||2048|488281250\n' > "$LC/inv/sdb.parts"
list_drawers() { echo sda; echo sdb; }
partition_node() { echo "/dev/$1$2"; }
_fs_of() { echo ntfs; }
capture_ntfs_hibernation_reason() { case " $HIB " in *" ${1#/dev/} "*) echo "חלונות במחיצה ${1#/dev/} נכבה ב-Fast Startup — x"; return 1 ;; esac; return 0; }
disk_port() { echo 2; }; disk_drawer() { echo "3|SATA 2"; }   # #2084: the source is "דיסק 3", never "sda"
ntfsresize() {
    for _a; do _n=$_a; done; echo "${_n#/dev/}" >> "$RUN_DIR/ntfsresize.log"
    case " $DIRTY " in *" ${_n#/dev/} "*) echo "ERROR: NTFS is inconsistent. Run chkdsk /f on Windows then reboot it TWICE!"; return 1 ;; esac
    echo "You might resize at 100000000000 bytes or 100000 MB (freeing 300000 MB)."
}
lc_copy_size() { :; }
disk_serial() { echo "S-$1"; }
echo 0 > "$RUN_DIR/min_free_gb"
lc_measure sda; echo "rc=$?"
echo "measure $(cat "$LC/measure/sda")"
echo "verdict $(lc_verdict_for sda sdb)"
echo "read $(tr '\n' ' ' < "$RUN_DIR/ntfsresize.log")"
'''


def measure(tmp_path: Path, dirty: str, hib: str = "") -> dict[str, str]:
    out = run(tmp_path, f'DIRTY={dirty!r}; HIB={hib!r}; ' + SETUP)
    assert out.returncode == 0, out.stderr
    return {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] if " " in ln else "" for ln in out.stdout.splitlines()}


def test_a_target_with_an_inconsistent_ntfs_is_offered(tmp_path):
    res = measure(tmp_path, "sdb1")
    assert res["verdict"].startswith("shrink|"), res   # the source fits it once shrunk: offered
    assert "sdb" not in res["read"], res                # the target's file system is never read


def test_an_inconsistent_source_is_refused_and_says_it_is_the_source(tmp_path):
    res = measure(tmp_path, "sda1")
    v = res["verdict"].split("|")
    assert v[0] == "unchecked", res
    assert v[2].startswith("במקור (דיסק 3) — מחיצה 1 בדיסק sda לא עקבית (NTFS)"), v[2]   # #2084: the disk by its number
    assert "chkdsk /f" in v[2] and not v[2].endswith("."), v[2]   # the screen adds ". לא ישמש כיעד."


def test_the_screen_never_draws_two_dots():
    src = (REPO / "native-gui" / "src" / "screens_local.c").read_text(encoding="utf-8")
    fn = src[src.index("static void verdict_text("):src.index("static int blocked(")]
    assert "lc_no_dot(f->reason, why, sizeof why)" in fn, fn
    assert '"%s. לא ישמש כיעד.", f->reason' not in fn and '"%s%s. לא ישמש כיעד.", f->reason[0] ? " — " : "", f->reason' not in fn


def test_a_hibernated_source_says_it_in_the_words_of_mockup_09_without_a_device_name(tmp_path):
    """#2084 (mockup 09, section 3; until #2069): "המקור (דיסק 3) במצב שינה של Windows" and the one
    command -- not "sda", not "קלטו מחדש" (a local clone has no capture)."""
    v = measure(tmp_path, "", hib="sda1")["verdict"].split("|")
    assert v[0] == "unchecked", v
    assert v[2] == ("המקור (דיסק 3) במצב שינה של Windows — אי אפשר לכווץ אותו לדיסק הזה, שקטן ממנו. "
                    "ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי"), v[2]
    assert "sda" not in v[2] and "קלטו" not in v[2]
