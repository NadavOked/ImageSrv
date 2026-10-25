"""#1907 / QA-G47 -- the read-back check list that did not come down (Nadav 22/10).

The agent downloads the server's list of each partition once for the whole round
(``_rb_plan``, agent/lib/readback.sh). Until #1907 one failed ``http_get`` (404,
500, a timeout -- after curl's own ``--retry``) put every drawer of the round at
``unverified`` with the same words as "the image has no list yet".

The decision:
  * the list is fetched again -- ``RB_LIST_TRIES`` tries, ``RB_LIST_PAUSE_S`` apart,
    no new try ``RB_LIST_WAIT_S`` after the plan began (a ceiling, not a hope);
  * still none: the drawer is done, ``readback: unverified`` with its own reason,
    ``readback_why: list_not_fetched`` -- the round does not fail;
  * said in orange in the cloner's GUI (``drawer_rb=`` -> "הושלם · לא אומת — הרשימה
    לא הורדה"), in the console ("לא אומת — הרשימה לא הורדה") and in Hebrew in the
    journal (``readback_list_not_fetched``).

The stand-ins are test_readback_agent_1805.py's; ``http_get`` here fails the first N
calls for a partition (``$BOX/fail.<index>`` holds N) and serves the list after.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import BASH, posix
from test_readback_agent_1805 import box_for, manifest, prelude, run
from test_timeouts import run_sh

REPO = Path(__file__).resolve().parent.parent
NOLIST = "לא אומת — הרשימה לא הורדה"

_SH = requires_native(("bash", BASH), "jq", why="the manifest is read with jq, as on the machine")

FLAKY_GET = (
    'http_get() { i=${1##*/}; echo "get ${1##*/api/v1/images/}" >> "$BOX/calls"\n'
    '  n=$(cat "$BOX/fail.$i" 2>/dev/null || echo 0)\n'
    '  if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$BOX/fail.$i"; return 22; fi\n'
    '  cat "$BOX/served.$i"; }\n'
)


def _drawers(box: Path, env: str = "RB_LIST_PAUSE_S=0") -> dict:
    (box / "m.json").write_text(json.dumps(manifest("sample")), encoding="utf-8", newline="\n")
    res = run(box, f"{FLAKY_GET}{env}\n"
                   f'RB_SERVER=http://s RB_IMAGE=img_x readback_drawers {posix(box / "m.json")!r} sdb sdc; echo "RC $?"')
    for d in ("sdb", "sdc"):
        why = box / "run" / "targets" / d / "readback_why"
        res["t"][d]["code"] = why.read_text(encoding="utf-8").strip() if why.exists() else None
    return res


def _gets(res: dict, index: int) -> int:
    return res["calls"].count(f"get img_x/readback/{index}")


@_SH
def test_a_list_that_failed_once_comes_down_on_the_next_try(tmp_path):
    box = box_for(tmp_path)
    (box / "fail.3").write_text("2", newline="\n")              # 404/timeout twice, then the list
    res = _drawers(box)
    assert res["rc"] == 0, res["raw"]
    assert _gets(res, 3) == 3, res["calls"]
    for d in ("sdb", "sdc"):
        t = res["t"][d]
        assert (t["state"], t["error"], t["readback"], t["code"]) == ("verifying", "", "sampled", None), res["raw"]


@_SH
def test_a_list_that_never_comes_down_is_done_orange_never_failed(tmp_path):
    box = box_for(tmp_path)
    (box / "served.3").unlink()                                   # the server has no list file: 404 every time
    res = _drawers(box, "RB_LIST_PAUSE_S=0 RB_LIST_TRIES=3")
    assert res["rc"] == 0, res["raw"]                             # the round goes on
    assert _gets(res, 3) == 3, res["calls"]                       # tried again, and stopped
    for d in ("sdb", "sdc"):
        t = res["t"][d]
        assert (t["state"], t["error"], t["readback"]) == ("verifying", "", "unverified"), res["raw"]
        assert t["code"] == "list_not_fetched", res["raw"]
        assert t["why"].startswith("רשימת הבדיקה לא הורדה מהשרת או אינה של המניפסט (3 ניסיונות)"), t["why"]
    out = run_sh(prelude(box) + "build_progress ses_1 aa:bb")
    entry = next(t for t in json.loads(out.strip().splitlines()[-1])["targets"] if t["dev"] == "sdb")
    assert (entry["readback"], entry["readback_why"]) == ("unverified", "list_not_fetched"), entry


@_SH
def test_the_ceiling_stops_the_tries_before_their_count(tmp_path):
    box = box_for(tmp_path)
    (box / "served.3").unlink()
    res = _drawers(box, "RB_LIST_PAUSE_S=0 RB_LIST_TRIES=50 RB_LIST_WAIT_S=0")
    assert _gets(res, 3) == 1, res["calls"]                       # past the ceiling: no second try
    assert res["t"]["sdb"]["code"] == "list_not_fetched"


@_SH
def test_an_image_without_lists_keeps_its_own_reason(tmp_path):
    """No list in the manifest is "not hashed yet", not "did not come down": no code, no GET."""
    box = box_for(tmp_path)
    (box / "m.json").write_text(json.dumps(manifest("sample", kinds={})), encoding="utf-8", newline="\n")
    res = run(box, f"{FLAKY_GET}RB_LIST_PAUSE_S=0\n"
                   f'RB_SERVER=http://s RB_IMAGE=img_x readback_drawers {posix(box / "m.json")!r} sdb sdc')
    assert res["t"]["sdb"]["readback"] == "unverified", res["raw"]
    assert not (box / "run" / "targets" / "sdb" / "readback_why").exists()
    assert not any(c.startswith("get ") for c in res["calls"])


@requires_native("jq", why="json_get reads the image name with jq")
def test_the_cloner_screen_gets_the_drawer_rb_record_after_that_drawer(tmp_path):
    """``cloner_gui_state`` writes ``drawer_rb=<dev>|list_not_fetched`` right after that
    drawer's ``drawer=`` -- and nothing for a drawer with any other outcome."""
    from test_cloner_gui import PORTS, PRELUDE, env, sh, target
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    for dev in ("sda", "sdb"):
        target(run, dev, "done", 100, 100, "")
        (run / "targets" / dev / "readback").write_text("unverified\n", newline="\n")
    (run / "targets" / "sda" / "readback_why").write_text("list_not_fetched\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    rb = [n for n, line in enumerate(lines) if line.startswith("drawer_rb=")]
    assert [lines[n] for n in rb] == ["drawer_rb=sda|list_not_fetched"], lines
    assert lines[rb[0] - 1].startswith("drawer=1|sda|done|"), lines


# --- the server: the journal and drawer_list --------------------------------------

MAC = "78:ac:c0:9b:19:07"


def _session(conn, sid: str) -> None:
    from server import db
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at)"
        " VALUES (?, 'grp_CLONERS', 'img_x', 'p', 1, 300, 'running', 't', ?, 0, ?)",
        (sid, db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES (?, ?, 'writing', ?)", (sid, MAC, db.now_iso()))
    conn.commit()


def _t(dev: str, **kw) -> dict:
    return {"dev": dev, "state": "done", "bytes_written": 1, "bytes_total": 1, **kw}


def test_a_done_drawer_whose_list_did_not_come_down_is_in_the_journal_once(tmp_path):
    from server import db, journal_he, reports
    conn = db.connect(tmp_path / "imagectl.db")
    _session(conn, "ses_g47")
    targets = [_t("sdb", readback="unverified", readback_reason="r", readback_why="list_not_fetched"),
               _t("sdc", readback="unverified", readback_reason="no list yet"),
               _t("sdd", readback="sampled", readback_why="list_not_fetched")]   # a stray code on sampled: not this
    assert reports.ingest(conn, {"session_id": "ses_g47", "mac": MAC, "state": "done", "targets": targets})["ok"]
    assert reports.ingest(conn, {"session_id": "ses_g47", "mac": MAC, "state": "done", "targets": targets})["ok"]
    rows = [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'readback_list_not_fetched'")]
    assert rows == [f"{MAC} sdb in ses_g47"], rows
    assert journal_he.EVENTS_HE["readback_list_not_fetched"] == "הדיסק הושלם ולא אומת — רשימת הבדיקה לא הורדה מהשרת"
    assert journal_he.severity("readback_list_not_fetched") == "warn"


def test_drawer_list_carries_the_reason_only_with_unverified(tmp_path):
    from server import db, room
    conn = db.connect(tmp_path / "imagectl.db")
    db.net_seen(conn, MAC, "10.0.0.7", disks_json=json.dumps(
        [{"dev": d, "serial": f"S-{d}", "port": n} for n, d in enumerate(("sdb", "sdc", "sdd"), 1)]))
    member = {"targets_json": json.dumps([
        _t("sdb", readback="unverified", readback_reason="r", readback_why="list_not_fetched"),
        _t("sdc", readback="unverified", readback_reason="no list yet"),
        _t("sdd", readback="sampled", readback_reason="s", readback_why="list_not_fetched")])}
    got = {d["dev"]: (d.get("readback"), d.get("readback_why")) for d in room.drawer_list(conn, MAC, set(), member)}
    assert got == {"sdb": ("unverified", "list_not_fetched"), "sdc": ("unverified", None),
                   "sdd": ("sampled", None)}, got


# --- the console: readbackLine -----------------------------------------------------

NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_says_the_list_did_not_come_down_in_orange(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    fn = re.search(r"^function readbackLine\(d\) \{.*?^\}", js, re.M | re.S)
    assert fn, "readbackLine is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text("const esc = (x) => String(x);\n" + fn.group(0) + """
console.log(JSON.stringify({
  nolist: readbackLine({ readback: "unverified", readback_why: "list_not_fetched", readback_reason: "r" }),
  nohash: readbackLine({ readback: "unverified", readback_why: null, readback_reason: "r" }),
}));
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert NOLIST in got["nolist"] and 'class="dline warn"' in got["nolist"], got
    assert NOLIST not in got["nohash"] and "אין עדיין רשימת בדיקה" in got["nohash"], got


# --- the cloner's GUI: the row of that drawer, in orange -----------------------------

def _gui_requirements():
    from test_cloner_gui import _pkgconfig
    return requires_native(
        ("cc", shutil.which("cc") or shutil.which("gcc")),
        ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
        why="native-gui נבנה על המעבדה בלבד")


@_gui_requirements()
def test_the_cloner_row_of_a_drawer_whose_list_did_not_come_down_is_orange(tmp_path):
    from test_cloner_gui import _build_gui
    from test_room_precheck_native_432_65 import _render
    from test_cloner_gui import _count_color, _png_rgb
    from test_room_screens_lc_design import CDISKS, _drawer
    binary = _build_gui(tmp_path)
    done = _drawer(1, "sda", "done", 1) + _drawer(3, "sdc", "done", 1)
    _render(binary, "cloner=office365\n" + CDISKS + done + "message=x|y\n", tmp_path / "plain")
    _render(binary, "cloner=office365\n" + CDISKS + done + "drawer_rb=sdc|list_not_fetched\nmessage=x|y\n",
            tmp_path / "nolist")

    def orange(name: str) -> int:   # theme.c .warn (light) -- the colour of a done row's warning line (#943)
        _w, _h, ch, px = _png_rgb(tmp_path / f"{name}-cloner-light.png")
        return _count_color(px, ch, (0xC2, 0x54, 0x00), tol=40)
    assert orange("nolist") > orange("plain") + 100, (orange("nolist"), orange("plain"))
