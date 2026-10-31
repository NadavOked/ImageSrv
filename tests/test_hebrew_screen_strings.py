"""כל מחרוזת שמגיעה למסך המפעיל היא עברית (#1503).

ברזל 16/10: כרטיס שגיאה בגואי של מחשב הבנייה הראה "State could not be
verified / Check connection; see agent log". הכלל של נדב: כל מה שהמפעיל
קורא — בגואי הנייטיב ובקונסולה — הוא עברית RTL; טוקנים טכניים (sda,
chkdsk, כתובות, שמות קבצים) מותרים *בתוך* המשפט. לוגים (agent.log,
journal) נשארים באנגלית.

הטסט הזה הוא השומר. הוא סורק את **נקודות הכתיבה למסך** — לא כל מחרוזת
בקוד — ונופל על משפט ASCII בלבד (שלוש מילים ומעלה) באחת מהן:

* סוכן (‏sh): ‏`message=` / `title=` / `toast=` ל-state של הגואי, הארגומנט
  הראשון של `gui_error`, שדה ה-error של יעד (‏`target_set … failed|skipped|
  waiting|replacing`, ‏`fail_written_target`, ‏`_capture_failed`,
  ‏`_direct_failed`, ‏`_drivers_fail`, ‏`_bootentry_fail`), והמשתנים שזורמים
  אליו (‏`PLAN_ERROR`, ‏`SHRINK_ERROR`, ‏`LC_ERROR`, ‏`GROW_ERROR`, ‏`_dr_why`,
  ‏`_bs_why`, ‏`_sr1_why`) ושדה `"error":` של hostname.
* גואי נייטיב (‏C): מחרוזת ראשונה בקריאות הציור (‏`rtl`, ‏`text_make`,
  ‏`key_button`, ‏`draw_pill`, …) ובהשמה ל-`title`/`sub`/`form_error`/`toast`.
* אשף ההתקנה: כל קוד כשל של המנוע שיש לו שדה — יש לו גם שורה בעברית.
* יומן הקונסולה: כל פירוט אנגלי שנכתב ל-`journal(…)` יש לו כלל תרגום.
* מסך השרת (DCUI): כל הודעת ActionResult מתורגמת ב-`view.he`.
* תשובות HTTP של הקונסולה: ‏`HTTPException` בלי עברית — רק ברשימה הלבנה
  (קודי מכונה וממשקי סוכן).

מה לא נבדק, בכוונה: מסך הטקסט של הקונסולה הלינוקסית (‏`ui.sh`,
‏`dcui/app.py` ב-80x25) — היא אינה מציירת עברית (#1182); ‏`log …` ו-`>&2`;
ופלט גולמי של כלי שמוצג מאחורי "פרטים טכניים".
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from agentlib import lib_files

import pytest

REPO = Path(__file__).resolve().parent.parent
HEBREW = re.compile(r"[֐-׿]")

#: טוקנים טכניים שמותר שיישארו לטיניים בתוך משפט עברי, ושאינם "מילה".
TOKENS = frozenset("""
sgdisk sfdisk mkswap mkfs partclone zstd fanout udp sender receiver xxh64 sha256
ntfs ntfsfix ntfsresize btrfs xfs xfs_growfs ext4 vfat uefi bios esp msr gpt mbr
efibootmgr bootnext boot bootmgr devicepath software system hive hivewrite
dhcp dns ip mac guid uuid partuuid smart ata nvme sata ssd hdd usb rc stage
mb gb mib gib kb bytes byte iso http https tftp wol dcui gui
""".split())

#: מילים שהן מזהי קוד/מצב ולא משפט (‏state=failed וכד').
WORD = re.compile(r"[A-Za-z][A-Za-z']+")


def english_words(text: str) -> list[str]:
    """המילים הלטיניות שמרכיבות משפט: בלי ‏$משתנים, ‏--דגלים, ‏key=value,
    נתיבים וטוקנים טכניים."""
    text = re.sub(r"\$\{[^}]*\}|\$\([^)]*\)|\$[A-Za-z_][A-Za-z0-9_]*", " ", text)
    text = re.sub(r"--?[A-Za-z][\w-]*", " ", text)
    text = re.sub(r"\b[\w.-]+=\S*", " ", text)
    text = re.sub(r"[/\\][\w./\\-]+", " ", text)
    return [w for w in WORD.findall(text) if w.lower() not in TOKENS and len(w) > 1]


def is_english_sentence(text: str) -> bool:
    """ASCII בלבד (בלי אות עברית), שלוש מילים לטיניות ומעלה."""
    return not HEBREW.search(text) and len(english_words(text)) >= 3


# --------------------------------------------------------------------------- #
# סוכן (sh)
# --------------------------------------------------------------------------- #

_STR = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
#: סינק -> ביטוי שתופס את המחרוזת *הראשונה* אחריו.
SH_SINKS = {
    "message=": re.compile(r"message=([^|\n\\']+)"),
    "title=": re.compile(r"(?<![\w.])title=([^|\n\\']+)"),
    "toast=": re.compile(r"toast=([^|\n\\']+)"),
    "gui_error": re.compile(r"gui_error\s+" + _STR),
    "target_set failed": re.compile(r"target_set\s+\S+\s+\"?(?:failed|skipped|waiting|replacing)\"?\s+\\?\s*" + _STR),
    "_fail_alive": re.compile(r"_fail_alive\s+\S+\s+" + _STR),
    "fail_written_target": re.compile(r"fail_written_target\s+\S+\s+\\?\s*" + _STR),
    "_capture_failed": re.compile(r"_capture_failed\s+\S+\s+\\?\s*" + _STR),
    "_direct_failed": re.compile(r"_direct_failed\s+\S+\s+\\?\s*" + _STR),
    "_drivers_fail": re.compile(r"_drivers_fail\s+" + _STR),
    "_bootentry_fail": re.compile(r"_bootentry_fail\s+\S+\s+" + _STR),
    "ERROR var": re.compile(r"\b(?:PLAN_ERROR|SHRINK_ERROR|LC_ERROR|GROW_ERROR|_dr_why|_bs_why|_sr1_why)=" + _STR),
    "json error": re.compile(r'"error":"([^"]*)"'),
}


def sh_files() -> list[Path]:
    files = [p for p in (REPO / "agent").rglob("*") if p.is_file() and p.suffix not in {".c", ".h"}]
    files += [REPO / "installer" / "gui-bridge.sh", REPO / "installer" / "lib" / "fielderrors.sh"]
    return sorted(files)


def sh_violations() -> list[str]:
    bad = []
    for path in sh_files():
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or ">&2" in line and "gui_error" not in line:
                continue
            for name, rx in SH_SINKS.items():
                for m in rx.finditer(line):
                    text = next((g for g in m.groups() if g), "")
                    if is_english_sentence(text):
                        bad.append(f"{path.relative_to(REPO)}:{n} [{name}] {text[:90]}")
    return bad


def test_the_sh_scan_sees_the_sinks_it_was_written_for():
    """בקרה: הסריקה מזהה כל סוג סינק. בלי זה ירוק יכול להיות סריקה עיוורת."""
    probes = {
        "message": "printf 'message=State could not be verified|Check connection\\n'",
        "gui_error": "gui_error 'Login was refused or could not be verified'",
        "target_set failed": 'target_set "$1" "failed" "disk too small by 5 MiB"',
        "fail_written_target": 'fail_written_target "$_d" "partition 3 has no file but is not swap"',
        "ERROR var": 'PLAN_ERROR="malformed restore plan -- disk untouched"',
        "json error": """printf '{"ok":false,"error":"could not mount the windows partition"}\\n'""",
    }
    for name, line in probes.items():
        hits = []
        for rx_name, rx in SH_SINKS.items():
            for m in rx.finditer(line):
                text = next((g for g in m.groups() if g), "")
                hits.append(is_english_sentence(text))
        assert any(hits), f"הסריקה לא תפסה את המקרה {name}: {line}"


def test_a_hebrew_first_line_with_technical_tokens_passes():
    assert not is_english_sentence("מחיצה 3: mkswap נכשל")
    assert not is_english_sentence("נכתבו $_written מתוך $_expected מחיצות")
    assert not is_english_sentence("sgdisk -c rc=2")
    assert is_english_sentence("partition 3 has no file but is not swap")


def test_no_english_sentence_is_written_to_a_screen_field_in_the_agent():
    bad = sh_violations()
    assert not bad, "משפט באנגלית בשדה שמגיע למסך (עברית עם טוקנים טכניים בתוכה):\n" + "\n".join(bad)


# --------------------------------------------------------------------------- #
# גואי נייטיב (C)
# --------------------------------------------------------------------------- #

C_SINKS = re.compile(
    r"""(?:\brtl\(|\brtl_block_make\(|\btext_make\(|\bkey_button\(|\bdraw_pill\(|\bhead_make\(|"""
    r"""\binstall_text_r\(|\binstall_text\(|\bdraw_label\(|\bdraw_metric\()[^"]*?"((?:[^"\\]|\\.)*)\""""
)
C_ASSIGN = re.compile(
    r"""snprintf\s*\([^;]*?\b(?:form_error|room_error|class_error|toast|done_title|done_sub)\b[^"]*"((?:[^"\\]|\\.)*)\""""
)
#: מסגרות הדגמה שמציירות את המסכים ל-PNG ושל מנגנון ההתקנה: ‏title/log שם הם
#: הלוג הטכני שמאחורי "פרטים טכניים", לא כותרת מסך.
C_SKIP = {"install.c"}


def test_native_gui_literals_are_hebrew():
    bad = []
    for path in sorted((REPO / "native-gui" / "src").glob("*.c")):
        if path.name in C_SKIP:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip().startswith(("*", "/*", "//")):
                continue
            for rx in (C_SINKS, C_ASSIGN):
                for m in rx.finditer(line):
                    text = m.group(1)
                    if is_english_sentence(text):
                        bad.append(f"{path.name}:{n} {text[:80]}")
    assert not bad, "מחרוזת ASCII-בלבד מצוירת בגואי:\n" + "\n".join(bad)


def test_installer_inventory_machine_values_are_translated_at_the_display_boundary():
    draw = (REPO / "native-gui" / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    assert 'strcmp(raw,"empty")' in draw and 'snprintf(out,n,"ריק")' in draw
    assert 'strcmp(raw,"unknown")' in draw and 'snprintf(out,n,"לא ידוע")' in draw
    assert '"%s:%d מחיצות (%s)"' in draw
    assert '"מערכת קבצים %s"' in draw
    assert 'strcmp(d->bus,"unknown")' in draw
    assert 'strcmp(n->link,"unknown")' in draw
    assert 'install_text_r(cr,t,d->iso?"מדיית ההתקנה":(d->has[0]?d->has:"—")' not in draw


def test_the_c_scan_sees_a_drawn_literal():
    probe = 'Text t = rtl(g, 12, 400, "This disk could not be verified", w);'
    m = C_SINKS.search(probe)
    assert m and is_english_sentence(m.group(1))


# --------------------------------------------------------------------------- #
# אשף ההתקנה
# --------------------------------------------------------------------------- #

def _bridge() -> str:
    """הגשר ומה שהוא טוען: טבלאות הקוד->שדה->עברית יושבות ב-lib/fielderrors.sh."""
    installer = REPO / "installer"
    bridge = (installer / "gui-bridge.sh").read_text(encoding="utf-8")
    assert '. "$BRIDGE_DIR/lib/fielderrors.sh"' in bridge, "הגשר אינו טוען את fielderrors.sh"
    return bridge + "\n" + (installer / "lib" / "fielderrors.sh").read_text(encoding="utf-8")


def test_every_installer_failure_code_with_a_field_has_a_hebrew_line():
    text = _bridge()
    fields = re.search(r"field_for_code\(\) \{(.*?)\n\}", text, re.S).group(1)
    heb = re.search(r"he_for_code\(\) \{(.*?)\n\}", text, re.S).group(1)
    codes = set(re.findall(r"\b([a-z]+(?:-[a-z]+)+)\b", fields))
    assert len(codes) >= 15, codes
    missing = {c for c in codes if not re.search(rf"\b{re.escape(c)}\)", heb)
               and not re.search(rf"\b{re.escape(c)}\|", heb) and f"|{c})" not in heb}
    assert not missing, f"קוד כשל בלי שורה בעברית ב-he_for_code: {sorted(missing)}"
    for line in re.findall(r"printf '%s' '([^']*)'", heb):
        assert HEBREW.search(line), f"שורה בלי עברית ב-he_for_code: {line}"


def test_the_installer_field_error_is_not_the_engine_english_line():
    text = _bridge()
    assert 'emit "errors.$(field_for_code "${_ev_code:-unknown}")" "$_ev_msg"' not in text
    assert 'he_for_code "${_ev_code:-unknown}"' in text


# --------------------------------------------------------------------------- #
# שאלה שהמכונה ממתינה עליה (prompt ב-hello → "ממתין למפעיל: …" בקונסולה)
# --------------------------------------------------------------------------- #

#: ‏`attended_start "…"` / ‏`attended "…" cmd` עם שאלה קבועה — מה שעולה כ-`prompt` כמו שהוא.
ATTENDED_QUESTION = re.compile(r"""\battended(?:_start)?\s+"([^"$]+)"(?:\s|;|$)""")


def _attended_questions() -> list[tuple[str, str]]:
    found = []
    for path in sh_files():
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for m in ATTENDED_QUESTION.finditer(line):
                found.append((f"{path.relative_to(REPO)}:{n}", m.group(1)))
    return found


def _prompt_he() -> dict[str, str]:
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    block = re.search(r"const PROMPT_HE = \{(.*?)\};", js, re.S)
    assert block, "PROMPT_HE לא נמצא ב-console.js"
    pairs = re.findall(r'(?:"([^"]*)"|([A-Za-z_]\w*))\s*:\s*"([^"]*)"', block.group(1))
    return {quoted or bare: value for quoted, bare, value in pairs}


def test_every_fixed_attended_question_has_a_hebrew_prompt_line():
    """rc6, 18/10: בדף המוניטור משכפל 2 הוצג "ממתין למפעיל: Local clone screen: from
    the server, or a local clone". השאלה של `attended_start` עולה ב-hello כ-`prompt`,
    ו-promptHe ב-console.js מציג טקסט שאין לו תרגום כמו שהוא. לכל שאלה קבועה באנגלית
    בסוכן — שורה עברית ב-PROMPT_HE, מילה במילה."""
    questions = _attended_questions()
    texts = {q for _, q in questions}
    # בקרה: הסריקה רואה את שלוש השאלות הקבועות של השיכפול המקומי (סריקה עיוורת = ירוק שקרי).
    assert "Local clone screen: from the server, or a local clone" in texts, questions
    assert "Restore an image from the server onto this machine's drawers" in texts, questions
    table = _prompt_he()
    assert table.get("menu") == "תפריט", table   # הפרסור קורא גם מפתח בלי גרשיים
    bad = [f"{where} {q}" for where, q in questions
           if is_english_sentence(q) and not HEBREW.search(table.get(q, ""))]
    assert not bad, "שאלה באנגלית שתגיע לקונסולה בלי שורה ב-PROMPT_HE:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- #
# יומן הקונסולה
# --------------------------------------------------------------------------- #

def _journal_details() -> list[tuple[str, str, str]]:
    found = []
    for path in sorted((REPO / "server").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and len(node.args) >= 3):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name != "journal" or not isinstance(node.args[1], ast.Constant):
                continue
            detail = node.args[2]
            if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
                text = detail.value
            elif isinstance(detail, ast.JoinedStr):
                text = "".join(v.value if isinstance(v, ast.Constant) else "7" for v in detail.values)
            else:
                continue
            found.append((str(path.relative_to(REPO)), node.args[1].value, text))
    return found


def test_every_english_journal_detail_has_a_translation_rule():
    from server.console.journal_he_detail import he_detail

    assert len(_journal_details()) > 80
    bad = []
    for where, event, text in _journal_details():
        prose = re.sub(r"\b[a-z_]+=\S*", "", text)
        if HEBREW.search(text) or not re.search(r"[A-Za-z]{3,}", prose):
            continue
        if not HEBREW.search(he_detail(event, text)):
            bad.append(f"{where} [{event}] {text}")
    # פירוט שהוא מזהה בודד ('admin', 'kiosk' …) אינו משפט: הם נבדקים בטסט הייעודי.
    bad = [b for b in bad if len(english_words(b.split("] ", 1)[1])) >= 2 or " " in b.split("] ", 1)[1]]
    assert not bad, "פירוט אנגלי ביומן בלי כלל ב-journal_he_detail:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- #
# מסך השרת (DCUI)
# --------------------------------------------------------------------------- #

def test_every_dcui_action_message_is_translated_for_the_graphic_screen():
    from server.dcui import view

    # 16/10: pairing.py (F4 on a secondary) writes ActionResults too.
    trees = [(name, ast.parse((REPO / "server" / "dcui" / name).read_text(encoding="utf-8")))
             for name in ("actions.py", "pairing.py")]
    messages = []

    def text_of(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "reboot" for v in node.values)
        return None

    for name, node in ((name, node) for name, tree in trees for node in ast.walk(tree)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ActionResult":
            for arg in node.args[1:]:
                if isinstance(arg, ast.IfExp):
                    parts = [arg.body, arg.orelse]
                elif isinstance(arg, (ast.Tuple, ast.List)):
                    parts = list(arg.elts)
                else:
                    parts = [arg]
                for part in parts:
                    text = text_of(part)
                    if text and re.search(r"[A-Za-z]{3,}", text):
                        messages.append((f"{name}:{node.lineno}", text))
    assert len(messages) > 25, len(messages)
    assert any(where.startswith("pairing.py") for where, _t in messages)
    untranslated = [f"{n} {t}" for n, t in messages if view.he(t) == t]
    assert not untranslated, "הודעת DCUI בלי תרגום ב-view.he:\n" + "\n".join(untranslated)


# --------------------------------------------------------------------------- #
# תשובות HTTP של הקונסולה
# --------------------------------------------------------------------------- #

#: קודי מכונה (‏JS מתרגם אותם) וממשקי סוכן — שם התשובה נקראת על ידי קוד, לא אדם.
HTTP_ENGLISH_OK = {
    ("server/console/auth.py", "password_change_required"),
    ("server/console/auth.py", "mfa_enrollment_required"),
    ("server/console/console_mfa.py", "builtin_no_mfa"),
    ("server/console/console_storage.py", "{}: {}"),
}
#: מודולים שהם ממשק סוכן↔שרת בלבד: התשובה נרשמת ביומן הסוכן.
AGENT_PROTOCOL = {"server/library/capture.py", "server/rounds/direct.py"}


def test_console_http_errors_are_hebrew():
    bad = []
    for path in sorted((REPO / "server").rglob("*.py")):
        rel = str(path.relative_to(REPO)).replace("\\", "/")
        if rel in AGENT_PROTOCOL:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "HTTPException"):
                continue
            args = [kw.value for kw in node.keywords if kw.arg == "detail"] + node.args[1:2]
            for arg in args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    text = arg.value
                elif isinstance(arg, ast.JoinedStr):
                    text = "".join(v.value if isinstance(v, ast.Constant) else "x" for v in arg.values)
                else:
                    continue
                if re.search(r"[A-Za-z]{3,}", text) and not HEBREW.search(text) and (rel, text) not in HTTP_ENGLISH_OK:
                    bad.append(f"{rel}:{node.lineno} {text}")
    assert not bad, "HTTPException באנגלית בקונסולה:\n" + "\n".join(bad)


@pytest.mark.parametrize("event,raw,expect", [
    ("unknown_mac", "aa:bb:cc:dd:ee:ff from 10.0.0.5", "aa:bb:cc:dd:ee:ff מ-10.0.0.5"),
    ("capture_done", 'img_1 "Win" (reconciled on start)', 'img_1 "Win" (הותאם בעלייה)'),
    ("settings_backup_downloaded", "backup.db 2048 bytes", "backup.db 2048 בייט"),
    ("login", "mfa_required", "נדרש קוד אימות"),
    ("smart_answered", "aa:bb 3 skip console:nadav", "aa:bb 3 דילוג בקונסולה · nadav"),
    ("dcui_auth_failure", "admin attempt=2", "admin · ניסיון 2"),
    ("net_config_refused", "eth0 pending change", "eth0 · יש שינוי ממתין לאישור"),
])
def test_journal_details_read_in_hebrew(event, raw, expect):
    from server.console.journal_he_detail import he_detail

    assert he_detail(event, raw) == expect


def test_a_hebrew_detail_and_an_unknown_event_pass_through_unchanged():
    from server.console.journal_he_detail import he_detail

    assert he_detail("capture_done", 'img_1 "וינדוס"') == 'img_1 "וינדוס"'
    assert he_detail("no_such_event", "a b c") == "a b c"


# --------------------------------------------------------------------------- #
# תחביר: עברית בתוך מחרוזת sh לא שוברת את הציטוט (#1503, סקירה אוטומטית)
# --------------------------------------------------------------------------- #

def _shell_scripts() -> list[Path]:
    agent = REPO / "agent"
    return sorted(lib_files(agent / "lib") + [agent / "init", agent / "imagesrv-agent",
                                                         REPO / "installer" / "gui-bridge.sh",
                                                         REPO / "installer" / "lib" / "fielderrors.sh"])


def test_no_ascii_apostrophe_sits_inside_a_hebrew_word_in_shell():
    """‏`הרג'יסטרי` בתוך printf '…' שבר את הציטוט. הגרש העברי הוא ‏׳ (U+05F3)."""
    rx = re.compile("([א-ת])'([א-ת])")
    bad = []
    for path in _shell_scripts():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if rx.search(line):
                bad.append(f"{path.relative_to(REPO)}:{n} {line.strip()[:80]}")
    assert not bad, "גרש ASCII בתוך מילה עברית (השתמשו ב-׳):\n" + "\n".join(bad)


@pytest.mark.parametrize("path", _shell_scripts(), ids=lambda p: p.name)
def test_every_agent_shell_script_parses(path):
    import shutil
    import subprocess

    shells = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]
    assert shells, "אין מעטפת לבדיקת תחביר"
    for shell in shells[:1]:
        r = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True,
                           encoding="utf-8", stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"{shell} -n {path.name}: {r.stderr}"
    busybox = shutil.which("busybox")
    if busybox:
        r = subprocess.run([busybox, "ash", "-n", str(path)], capture_output=True, text=True,
                           encoding="utf-8", stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"busybox ash -n {path.name}: {r.stderr}"
