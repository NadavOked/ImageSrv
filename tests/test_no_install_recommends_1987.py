"""‏#1987 — אף התקנת apt של השרת אינה מתקינה Recommends.

הכרעת נדב 22/10: השרת במכללה בלי חבילות שאיש לא ביקש. ‏#1975 מצא בהן חולשות
(‏python3-python-multipart, ‏python3-h2 ועוד) בנתיבים שאינם בשימוש — הן הגיעו
רק כ-Recommends. לכן כל `apt-get install` בסקריפטים (המתקין, כפתור העדכון,
מארח ה-VM, ה-ISO, בניית ה-initramfs) נושא `--no-install-recommends`, ותלות
שבאמת נדרשת נרשמת במפורש (‏PKGS / ‏tools/iso/packages.txt).

הטסט **סורק** את הסקריפטים ולא מחזיק רשימה של שורות: התקנה חדשה בלי הדגל
נופלת כאן גם אם איש לא זכר להוסיף אותה לטסט. טקסט בתוך מחרוזת (הודעה
למפעיל: "apt-get install נכשל") אינו פקודה ואינו נספר. ראיה חיובית שהסריקה
עובדת: כל אתרי ההתקנה הידועים נמצאו בה (‏`KNOWN_SITES`), אחרת סריקה שנשברה
ומצאה אפס הייתה ירוקה (עיקרון 5).
"""

from __future__ import annotations

import re
from pathlib import Path

from test_installer_packages import installer_pkgs

REPO = Path(__file__).resolve().parent.parent
SCAN_ROOTS = ("deploy", "installer", "tools", "agent", "boot")

#: אתרי ההתקנה של השרת שחייבים להימצא בסריקה — קובץ → מינימום הפעלות.
KNOWN_SITES = {
    "deploy/setup-boot-server.sh": 2,     # PKGS + nftables
    "deploy/vmupdate-host.sh": 1,         # חבילות עדכון ה-VM (גם מכפתור העדכון)
    "deploy/security-upgrade.sh": 1,      # כפתור העדכון: המפגרות + החדשות (#1803)
    "installer/lib/bootstrap.sh": 1,       # המתקין החי של ה-ISO
    "tools/build_initramfs.sh": 5,         # בסיס, ארגז כלים, GUI, ‏firmware-amd, כלי בנייה
}

_APT = re.compile(r"(?<![\w./$-])(apt-get|apt|\$APT|\$\{APT\})(?=\s)")
#: מה שאינו מתקין דבר: הורדה ל-pool וסימולציה (tools/iso/make-pool.sh).
_NOT_AN_INSTALL = {"--download-only", "-s", "--simulate", "--dry-run", "--just-print"}


def code_only(line: str) -> str:
    """השורה בלי הערה ובלי טקסט מילולי במחרוזות. נשארים: הקוד, הרחבות
    ‏`$VAR`/‏`${...}`, וגוף `$( ... )` גם כשהוא בתוך מרכאות."""
    out: list[str] = []
    stack: list[list] = [["code", None]]          # [סוג, עומק סוגריים | None ברמה העליונה]
    i = 0
    while i < len(line):
        kind = stack[-1][0]
        c = line[i]
        if kind == "'":
            if c == "'":
                stack.pop()
            out.append(" ")
            i += 1
            continue
        if kind == '"':
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == '"':
                stack.pop()
                out.append(" ")
                i += 1
                continue
            if line.startswith("$(", i):
                stack.append(["code", 0])
                out.append("$(")
                i += 2
                continue
            m = re.match(r"\$(\{[^}]*\}|\w+)", line[i:])
            if m:
                out.append(" " + m.group(0) + " ")
                i += len(m.group(0))
                continue
            out.append(" ")
            i += 1
            continue
        # קוד
        if c == "\\":
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "#" and (i == 0 or line[i - 1].isspace()):
            break
        if c in "\"'":
            stack.append([c, None])
            out.append(" ")
            i += 1
            continue
        if line.startswith("$(", i):
            stack.append(["code", 0])
            out.append("$(")
            i += 2
            continue
        depth = stack[-1][1]
        if c == "(" and depth is not None:
            stack[-1][1] += 1
        elif c == ")" and depth is not None:
            if depth == 0:
                stack.pop()
                out.append(" ) ")
                i += 1
                continue
            stack[-1][1] -= 1
        out.append(c)
        i += 1
    return "".join(out)


def logical_lines(text: str) -> list[tuple[int, str]]:
    """שורות אחרי חיבור המשכים (`\\` בסוף שורה), עם מספר השורה הראשונה."""
    lines: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, raw in enumerate(text.split("\n"), 1):
        if not buf:
            start = n
        if raw.rstrip().endswith("\\"):
            buf += raw.rstrip()[:-1] + " "
            continue
        lines.append((start, buf + raw))
        buf = ""
    if buf:
        lines.append((start, buf))
    return lines


def apt_installs(text: str) -> list[tuple[int, list[str]]]:
    """כל הפעלת `apt-get ... install` בקוד (לא בהודעה), עם הטוקנים שאחריה."""
    found = []
    for n, line in logical_lines(text):
        code = code_only(line)
        for m in _APT.finditer(code):
            rest = re.split(r"[;|&)]", code[m.end():], maxsplit=1)[0].split()
            if "install" in rest and not _NOT_AN_INSTALL & set(rest):
                found.append((n, rest))
    return found


def compliant(tokens: list[str]) -> bool:
    return "--no-install-recommends" in tokens or any(
        re.search(r"APT::Install-Recommends=(false|0)\b", t) for t in tokens)


def shell_scripts() -> list[Path]:
    paths = []
    for root in SCAN_ROOTS:
        for path in (REPO / root).rglob("*"):
            if not path.is_file() or path.suffix in (".py", ".md", ".txt", ".json", ".ps1"):
                continue
            if path.suffix == ".sh":
                paths.append(path)
                continue
            try:
                head = path.open("rb").readline()
            except OSError:
                continue
            if head.startswith(b"#!") and re.search(rb"\b(ba|da)?sh\b", head):
                paths.append(path)
    return sorted(paths)


def scan() -> dict[str, list[tuple[int, list[str]]]]:
    result = {}
    for path in shell_scripts():
        sites = apt_installs(path.read_text(encoding="utf-8", errors="replace"))
        if sites:
            result[path.relative_to(REPO).as_posix()] = sites
    return result


def test_the_scanner_tells_a_command_from_a_message() -> None:
    """הסורק עצמו: הודעה אינה פקודה, ופקודה בתוך `"$( ... )"` היא כן."""
    text = "\n".join([
        'echo "apt-get install נכשל (קוד $rc)"',
        'die "missing $tool (apt install mtools fdisk)"',
        '# apt-get install -y something  (comment)',
        'out="$(env X=1 "$APT" install -y -qq "${need[@]}" 2>&1)"',
        'run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${PKGS[@]}"',
        'apt-get -s -o APT::Install-Recommends=true install foo',
        'apt-get install -y --no-install-recommends \\',
        '    a b c',
        'n=${#need[@]}; apt-get update -qq',
    ])
    sites = apt_installs(text)
    assert [n for n, _ in sites] == [4, 5, 7], sites
    assert [compliant(t) for _, t in sites] == [False, False, True]


def test_every_known_install_site_was_found() -> None:
    """ראיה חיובית: סריקה שמצאה פחות מהידוע — נשברה, לא "הכול תקין"."""
    found = scan()
    short = {f: (len(found.get(f, [])), want) for f, want in KNOWN_SITES.items()
             if len(found.get(f, [])) < want}
    assert not short, f"הסריקה מצאה פחות התקנות מהידוע (נמצאו, נדרש): {short}"


def test_no_apt_install_brings_recommends() -> None:
    bad = [f"{f}:{n}: apt-get {' '.join(t)}" for f, sites in scan().items()
           for n, t in sites if not compliant(t)]
    assert not bad, ("התקנת apt בלי --no-install-recommends (#1987) — Recommends חוזרים לשרת:\n"
                     + "\n".join(bad))


def test_what_only_recommends_brought_and_the_server_uses_is_explicit() -> None:
    """שתי החבילות שנמדדו (‏testrunner, ‏22/10) כנשמטות עם הדגל ובשימוש השרת:
    ‏dbus (‏hostnamectl/timedatectl) ו-linux-image-amd64 (‏#904 ומכשיר ה-guestfs)."""
    pkgs = set(installer_pkgs())
    missing = {"dbus", "linux-image-amd64"} - pkgs
    assert not missing, f"חסר ב-PKGS של deploy/setup-boot-server.sh: {sorted(missing)}"
