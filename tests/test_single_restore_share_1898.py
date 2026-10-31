"""‏#1898 — הכרעת נדב (22/10): שאלת החלוקה גם בשחזור יחיד של מחשב בנייה.

dual-boot שנקלט עם כיווץ (‏C: לפני שורש הלינוקס): עד כאן רק המשכפל שאל
(‏`drawer_share_gate`), ובשחזור היחיד ‏`disk_fits` סירב בשם "יישארו רק X GB
ב-C:" — כי בלי `share.ok` ‏C: אינה גדלה. עכשיו `sr_share_gate`
(‏`agent/lib/restore/single_restore.sh`) מציג את אותה חלוקה (‏`share_plan`, ‏kind
‏`share`) על הגואי של ההתקדמות, לפני `pull_open` ולפני בייט ראשון:

* ‏`split` — ‏`targets/<disk>/share.ok`, והשחזור ממשיך (‏`expand_last` ו-`disk_fits`
  קוראים אותו).
* ‏`skip`, בלי תשובה עד התקרה, או בלי גואי — **לא נכתב דבר**, והסיבה בעברית על
  הגואי (‏`sr_refused share`).
* אימג' שאין בו מחיצה מכווצת לפני המועמד — אין שאלה, כמו קודם.

התשובה מגיעה מה-stdout של הגואי (‏`restore.out`), כמו `restore-cancel`: אין
כאן גשר שיהפוך אותה ל-`ask-decision`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_head_share_1898 import DISK, GIB, dual_boot
from test_single_restore_progress_gui import GUI, RLM, _pkgconfig, body, cards
from test_unicast_pull import AWAIT, MAC, RUN_TIMEOUT_S
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent

pytestmark = requires_native(("bash", BASH), "jq")

LIBS = ("common", "waits", "jsonq", "sysinfo", "progress", "pull", "ui", "guibridge", "clonergui",
        "restore", "expand", "grow", "ntfsunhib", "expandshare", "writemark", "slotnames", "diskfit", "askgui",
        "drawergate", "single_restore", "srgui", "guistate")

#: A GUI that answers the split question the way an operator would: once it
#: reads ask_prompt=<nonce>|share|... in its state, it says ask-<ANSWER>|<nonce>
#: on its stdout -- what main.c:emit_ask prints. ANSWER="" = nobody answers.
GUI_STUB = r'''#!/bin/sh
st=; for a in "$@"; do [ "$prev" = --state ] && st=$a; prev=$a; done
n=0; said=
while [ "$n" -lt 600 ]; do
    if [ -f "$st" ] && cat "$st" > "$RUN_DIR/gui.now" && ! cmp -s "$RUN_DIR/gui.now" "$RUN_DIR/gui.last"; then
        { cat "$RUN_DIR/gui.now"; echo "--"; } >> "$RUN_DIR/gui.seen"; cp "$RUN_DIR/gui.now" "$RUN_DIR/gui.last"
        LC_ALL=C grep -q '^message=' "$RUN_DIR/gui.now" && exit 0
        LC_ALL=C grep -qF 'השחזור הושלם' "$RUN_DIR/gui.now" && exit 0
        q=$(sed -n 's/^ask_prompt=\([0-9-]*\)|share|.*/\1/p' "$RUN_DIR/gui.now")
        if [ -n "$q" ] && [ -n "$ANSWER" ] && [ "$said" != "$q" ]; then printf 'ask-%s|%s\n' "$ANSWER" "$q"; said=$q; fi
    fi
    sleep 0.05; n=$((n + 1))
done
'''

#: run_restore as far as this needs it: it says whether the approval is there
#: when the write starts (expand_last and disk_fits read share.ok).
WRITE = ('run_restore() { if [ -e "$RUN_DIR/targets/$2/share.ok" ]; then echo "WRITE share.ok" >> "$RUN_DIR/trace"; '
         'else echo "WRITE no-share.ok" >> "$RUN_DIR/trace"; fi; '
         'echo done > "$RUN_DIR/state"; target_set "$2" done; return 0; }; ')


def restore(tmp_path: Path, manifest: dict, answer: str, *, gui: bool = True, wait_s: int = 30,
            disk_size: bool = True) -> dict:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    (run / "targets").mkdir()
    (tmp_path / "sys/block/sda").mkdir(parents=True)
    if disk_size:
        (tmp_path / "sys/block/sda/size").write_text(f"{DISK}\n", newline="\n")
    m = {**manifest, "total_compressed_bytes": 40_000_000_000, "name": "Dual Boot Lab"}
    (run / "manifest.src.json").write_text(json.dumps(m), encoding="utf-8")
    (run / "response.json").write_text(json.dumps({"name": "BUILD-01"}), encoding="utf-8")
    gui_bin = tmp_path / "station-gui"
    gui_bin.write_text(GUI_STUB, encoding="utf-8", newline="\n")
    gui_bin.chmod(0o755)
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} SYSROOT={shlex.quote(posix(tmp_path))} MAC={MAC!r} "
        f"SERVER=http://127.0.0.1:1 SR_GUI_BIN={shlex.quote(posix(gui_bin) if gui else '/nonexistent')} "
        f"ANSWER={answer!r} WAIT_SMART_GUI_S={wait_s} SR_GUI_TICK_S=0.2 D_ROLE=build "
        "AFTER_TASK_FILE=/nonexistent/after-task IMAGESRV_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=1; "
        + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
        + "attended_power_check() { :; }; disk_port() { echo 1; }; pick_internal_disk() { echo sda; }; "
        "stage_drivers() { :; }; smart_red() { return 1; }; target_ident_keep() { :; }; "
        "writemark_release_done() { :; }; "   # #1877: the mark is not what this checks
        'http_get() { cat "$RUN_DIR/manifest.src.json"; }; '
        "pull_post() { printf '{\"id\":\"ses_1\"}' > \"$3\"; echo 200; }; "
        "http_post_json() { :; }; progress_loop() { :; }; "
        + AWAIT + WRITE
        + 'single_restore_run img_1898; echo "RC=$?"; wait'
    )
    out_file = tmp_path / "out.txt"
    with out_file.open("w", encoding="utf-8") as sink:
        proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                              stdout=sink, stderr=subprocess.STDOUT, cwd=str(REPO),
                              stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S + wait_s)
    read = lambda name: (run / name).read_text(encoding="utf-8") if (run / name).exists() else ""  # noqa: E731
    return {"out": out_file.read_text(encoding="utf-8"), "rc": proc.returncode, "seen": read("gui.seen"),
            "trace": read("trace"), "log": read("agent.log"), "run": run}


def _asked(seen: str) -> list[str]:
    return [c for c in cards(seen) if "ask_prompt=" in c]


def test_split_writes_with_the_approval(tmp_path):
    r = restore(tmp_path, dual_boot(), "split")
    assert "RC=0" in r["out"], r["out"]
    assert r["trace"] == "WRITE share.ok\n", (r["trace"], r["out"])
    asked = _asked(r["seen"])
    assert asked, r["seen"]
    prompt = re.search(r"^ask_prompt=(.*)$", asked[0], re.M).group(1)
    assert re.fullmatch(r"\d+-0\|share\|2\|sda", prompt), prompt
    rows = re.findall(r"^ask_row=(.*)$", asked[0], re.M)
    # port 0 = "הדיסק"; C: (2) grows in place, the root (3) after it -- share_plan's rows
    assert [r_.split("|")[:3] for r_ in rows] == [["0", "2", str(20 * GIB)], ["0", "3", str(30 * GIB)]], rows
    assert all(int(r_.split("|")[3]) > int(r_.split("|")[2]) for r_ in rows), rows
    assert "share question (#1898) on sda -- split" in r["log"]
    assert "ask_prompt=" not in cards(r["seen"])[-1], "the question is gone once answered"


@pytest.mark.parametrize("answer,gui", [("skip", True), ("", True), ("split", False)])
def test_no_approval_writes_nothing_and_says_why_in_hebrew(tmp_path, answer, gui):
    """"דלג", אף אחד לא ענה עד התקרה, או שאין גואי לשאול בו — הדיסק לא שונה."""
    r = restore(tmp_path, dual_boot(), answer, gui=gui, wait_s=3)
    assert "WRITE" not in r["trace"], r["trace"]
    assert not (r["run"] / "targets/sda/share.ok").exists()
    assert "TEST-REBOOT: restore: the split of the room was not approved (#1898)" in r["out"], r["out"]
    if gui:
        last = body(cards(r["seen"])[-1])
        assert last == (f"message=השחזור לא התחיל — לא נכתב דבר|{RLM}"
                        "חלוקת המקום בין המחיצות לא אושרה · המחשב יופעל מחדש\n"), last


def test_an_image_with_nothing_shrunk_before_the_root_is_not_asked(tmp_path):
    r = restore(tmp_path, dual_boot(shrunk=False), "split")
    assert "RC=0" in r["out"], r["out"]
    assert r["trace"] == "WRITE no-share.ok\n", r["trace"]
    assert not _asked(r["seen"]), r["seen"]


def test_a_split_that_did_not_compute_writes_nothing(tmp_path):
    """‏share_plan נכשל (גודל הדיסק לא נקרא) — לא שאלה, ולא כתיבה בלי חלוקה."""
    r = restore(tmp_path, dual_boot(), "split", disk_size=False, wait_s=3)
    assert "WRITE" not in r["trace"] and not _asked(r["seen"]), (r["trace"], r["seen"])
    assert "TEST-REBOOT: restore: the split of the room did not compute (#1898)" in r["out"], r["out"]
    assert "לא הצלחנו לחשב את חלוקת המקום בין המחיצות" in cards(r["seen"])[-1]


# --- native: the question draws over --screen progress and answers (lab only) ---

native = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH), "jq",
    why="native-gui נבנה על המעבדה בלבד",
)

DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    (void)argc;
    static App a; a.theme = &THEME_LIGHT; a.force_progress = 1;   /* --screen progress */
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f); app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_write_to_png(s, argv[2]); cairo_surface_destroy(s);
    int prim = 0, sec = 0;
    for (int i = 0; i < a.nhits; i++) { prim |= a.hits[i].id == HIT_ASK_PRIMARY; sec |= a.hits[i].id == HIT_ASK_SECONDARY; }
    printf("screen=%d progress=%d ask=%d share=%d rows=%d primary=%d secondary=%d\n", a.screen, SCREEN_PROGRESS,
           a.st.ask_pending, a.st.ask_kind == ASK_SHARE, a.st.nask, prim, sec);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("sharedrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _drive(driver: Path, state: Path, png: Path) -> str:
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(driver), posix(state), posix(png)], capture_output=True, text=True,
                         encoding="utf-8", env=env, timeout=120, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout


@native
def test_the_split_question_is_drawn_and_answerable_over_the_progress_card(driver, tmp_path):
    """The card the restore's GUI reads while sr_ask waits (a stub GUI above
    recorded it): the dialog is drawn, its two answers are the only hits."""
    r = restore(tmp_path / "flow", dual_boot(), "split")
    asked = tmp_path / "asked.state"
    asked.write_text(_asked(r["seen"])[0], encoding="utf-8", newline="\n")
    out = _drive(driver, asked, tmp_path / "asked.png")
    assert "ask=1 share=1 rows=2 primary=1 secondary=1" in out, out
    screen, progress = (int(v) for v in re.findall(r"screen=(\d+) progress=(\d+)", out)[0])
    assert screen == progress, out
    plain = tmp_path / "plain.state"
    plain.write_text("".join(line for line in asked.read_text(encoding="utf-8").splitlines(True)
                             if not line.startswith(("ask_prompt=", "ask_row="))), encoding="utf-8", newline="\n")
    assert "ask=0" in _drive(driver, plain, tmp_path / "plain.png")
    assert (tmp_path / "asked.png").read_bytes() != (tmp_path / "plain.png").read_bytes()
