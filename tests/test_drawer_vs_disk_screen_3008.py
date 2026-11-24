"""#3008 (הכרעת נדב 12/11, נוסח 23/11): כבל/חריץ הוא סימון של **החיבור**, "נכשל בשיכפול הקודם" הוא של **הדיסק**.

הנוסח: "חיבור של <שם> לא תקין", בלי המילה SATA ובלי "מגירה". השם הוא מה שהמפעיל הגדיר בשרת: שם החיבור, אחרת
"דיסק N" של המגירה שהשרת מיפה; בלי שם ובלי מיפוי -- "חיבור של הדיסק לא תקין", ולעולם לא מספר שחושב כאן.

הטסט מקמפל עם gcc את הפונקציות האמיתיות של המסך (בלי cairo, כמו test_readonly_red_1742) ובודק את הטקסט, גם בחלון
ה-SMART (smartdlg.c) וגם בקונסולה (node).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
CC = shutil.which("cc") or shutil.which("gcc")
NODE = shutil.which("node")
CABLE_HE = "חיבור של {name} לא תקין"
CABLE_NONAME_HE = "חיבור של הדיסק לא תקין"
LAST_HE = "נכשל בשיכפול הקודם"


#: the connection's words exist (#3008); before it the same driver runs against the old signatures, so the test fails on the words
NEW_API = "cable_he" in (SRC / "screens_rounds.c").read_text(encoding="utf-8")


def _fn(file: str, name: str) -> str:
    text = (SRC / file).read_text(encoding="utf-8")
    start = re.search(r"\n(?:static )?(?:const char \*|void |int )" + name + r"\([^)]*\) \{", text)
    assert start, f"{name}() is not defined in {file}"
    depth, i = 1, start.end()
    while depth:
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start.start():i]


PRELUDE = r'''#include <stdio.h>
#include <string.h>
#define MAX_DRAWERS 8
#define LC_LABEL_MAX 120
typedef struct { int port, drawer; char name[104], smart[16], smart_reason[48]; } LcDisk;
const char *smart_reason_text(const char *reason);
int smart_level(const char *smart);
const char *smart_he(const char *smart);
'''


@requires_native(("cc", CC), why="the screen's functions are compiled alone, no cairo")
def test_the_screens_mark_the_drawer_for_a_cable_and_the_disk_for_its_own_failure(tmp_path):
    src = tmp_path / "t.c"
    src.write_text(
        PRELUDE
        + _fn("screens_rounds.c", "smart_he") + _fn("screens_rounds.c", "smart_reason_text")
        + _fn("screens_rounds.c", "smart_level") + _fn("screens_rounds.c", "eff_drawer")
        + (_fn("screens_rounds.c", "cable_he") + _fn("screens_rounds.c", "mapped_disk_name") + _fn("screens_local.c", "cable_mark") if NEW_API else "")
        + _fn("screens_local.c", "smart_reason_he")
        + _fn("smartdlg.c", "reason_words")
        + r'''
int main(int c, char **v) { (void)c; char o[256], w[256], nm[256];
  LcDisk d; memset(&d, 0, sizeof d); d.port = 1; d.drawer = atoi(v[2]);
  snprintf(d.name, sizeof d.name, "%s", strcmp(v[5], "-") ? v[5] : "");
  snprintf(d.smart, sizeof d.smart, "%s", v[3]); snprintf(d.smart_reason, sizeof d.smart_reason, "%s", v[4]);
#ifdef NEW_API
  if (!strcmp(v[1], "screen")) puts(smart_reason_he(&d, o, sizeof o));
  else { mapped_disk_name(d.name, d.drawer, nm, sizeof nm); reason_words(v[3], v[4], nm, w, sizeof w); puts(w); }
#else   /* the code before #3008: no connection name in either signature -- the test still runs and says what it says */
  (void)o; (void)nm; if (!strcmp(v[1], "screen")) puts(smart_reason_he(&d));
  else { reason_words(v[3], v[4], w, sizeof w); puts(w); }
#endif
  return 0; }
'''.replace("#include <stdio.h>", "#include <stdio.h>\n#include <stdlib.h>"),
        encoding="utf-8", newline="\n")
    # the PRELUDE has no stdlib; atoi needs it
    src.write_text("#include <stdlib.h>\n" + src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    built = subprocess.run([CC, "-Wall", "-Wextra", *(["-DNEW_API"] if NEW_API else []), "-o", str(tmp_path / "t"), str(src)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert built.returncode == 0, built.stderr

    def said(kind: str, drawer: int, smart: str, reason: str, name: str = "-") -> str:
        out = subprocess.run([str(tmp_path / "t"), kind, str(drawer), smart, reason, name], capture_output=True,
                             text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    for kind in ("screen", "dialog"):
        # a cable: the connection's mark, by the name the server maps it to -- not the disk's "failed the last clone"
        assert said(kind, 2, "failed_last", "cable") == CABLE_HE.format(name="דיסק 2"), kind
        assert LAST_HE not in said(kind, 3, "failed_last", "cable"), kind
        # the operator's own name for the connection wins over "דיסק N"
        assert said(kind, 3, "failed_last", "cable", "קוריאים · xx-7") == CABLE_HE.format(name="קוריאים · xx-7"), kind
        # no drawer and no name (port 1 is set in the driver: it must NOT be taken as a number), or outside the mapping
        assert said(kind, 0, "failed_last", "cable") == CABLE_NONAME_HE, kind
        assert said(kind, -1, "failed_last", "cable") == CABLE_NONAME_HE, kind
        # a name with no drawer is still the operator's name
        assert said(kind, 0, "failed_last", "cable", "דיסק 2") == CABLE_HE.format(name="דיסק 2"), kind
        # never the old words
        for words in (said(kind, 2, "failed_last", "cable"), said(kind, 0, "failed_last", "cable")):
            assert "SATA" not in words and "מגירה" not in words, (kind, words)
        # the disk's own failure keeps the disk's words
        assert said(kind, 2, "failed_last", "disk") == f"{LAST_HE} — סיבה: הדיסק", kind


def test_the_standby_and_dialog_callers_use_the_drawer_words():
    standby = (SRC / "screens_standby.c").read_text(encoding="utf-8")
    assert "cable_he(nm, cable_why" in standby and "cloner_slot_label(s, bay, nm" in standby
    assert "cable_he(conn_name, out, n)" in (SRC / "smartdlg.c").read_text(encoding="utf-8")


def test_the_build_machines_smart_window_names_its_disk_and_passes_the_drawer():
    """#2980 + #3008 together: the window calls the disk "הדיסק של המחשב" and still hands the drawer to the reason words."""
    fn = _fn("main.c", "disk_smart_ask")
    assert "d->dev, OWN_DISK_HE," in fn, "the disk's own name (#2980) is gone from the SMART window"
    assert "cname);" in fn and "mapped_disk_name(" in fn, "the connection's name (#3008) no longer reaches sdlg_open"


@requires_native(("node", NODE))
def test_the_console_says_drawer_for_a_cable_and_disk_for_a_failure(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    fn = re.search(r"^function cableDrawerText\(name\) \{.*?\}$", js, re.M)
    assert fn, "cableDrawerText() is gone from console.js"
    script = tmp_path / "t.js"
    script.write_text(fn.group(0) + "\nconsole.log(cableDrawerText(\"דיסק 3\")); console.log(cableDrawerText(null)); console.log(cableDrawerText(\"\"));\n", encoding="utf-8")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=30)
    assert out.stdout.split("\n")[:3] == ["חיבור של דיסק 3 לא תקין", CABLE_NONAME_HE, CABLE_NONAME_HE], out
    # the two call sites that said "נכשל בשיכפול הקודם" for a cable now branch on cause === "cable"
    assert js.count('cause === "cable" ? cableDrawerText(') + js.count('cause === "cable" ? esc(cableDrawerText(') == 2
