"""‏#1411 / #1459 — אין מקור apt חי בקובצי ההתקנה והעדכון של השרת.

הכרעת נדב (15/10): עדכוני Debian מגיעים לשרת **רק בגרסאות החתומות**; השרת לא
מושך מ-deb.debian.org (או מכל מאגר חבילות ברשת). הטסט הזה סורק את הקבצים
ש**רצים על השרת** — לא את `tools/iso/make-pool.sh` ו-`build-iso.sh` (רצים על
מארח הבנייה, שם apt ברשת הוא הכלי) ולא את `tools/lab/preseed.cfg` (מעבדה).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: הקבצים שרצים על השרת בהתקנה ובעדכון. תיקיות נסרקות ברקורסיה.
SCANNED = ["deploy", "installer", "tools/server-upgrade.sh", "tools/iso/firstboot.sh",
           "tools/iso/firstboot-answers.sh", "server/vm/vmhost.py"]
SUFFIXES = {".sh", ".py", ".list", ".service", ".conf", ""}

#: מקור רשת: שם מארח של מראת/אבטחה של דביאן-אובונטו, או שורת `deb`/`deb-src` ב-http(s).
NETWORK_APT = re.compile(
    r"(debian\.org|ubuntu\.com|security\.debian|ftp\.[a-z.]+\.debian)"
    r"|\bdeb(-src)?\s+(\[[^\]]*\]\s*)?https?://"
    r"|\[trusted=yes\]\s*(?!file:)\S+://"
    r"|apt-add-repository|add-apt-repository|apt-key\s+add",
    re.IGNORECASE)


def scanned_files() -> list[Path]:
    files: list[Path] = []
    for rel in SCANNED:
        path = REPO / rel
        found = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        files += [p for p in found if p.suffix in SUFFIXES and p.name != "README.md"]
    return files


def code_lines(path: Path):
    """שורות קוד בלבד — הערה שמסבירה מה הוסר אינה מקור."""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield number, line


def test_the_scan_covers_the_files_that_matter():
    """שומר על הסורק: ברשימה ריקה, 'אין ממצאים' אינו ראיה (עיקרון 5)."""
    names = {p.relative_to(REPO).as_posix() for p in scanned_files()}
    for must in ("deploy/vmupdate-host.sh", "deploy/setup-boot-server.sh",
                 "installer/lib/bootstrap.sh", "tools/server-upgrade.sh",
                 "deploy/security-upgrade.sh"):
        assert must in names, f"{must} חסר בסריקה"


def test_no_network_apt_source_in_install_and_update_files():
    hits = [f"{p.relative_to(REPO).as_posix()}:{n}: {line.strip()}"
            for p in scanned_files() for n, line in code_lines(p) if NETWORK_APT.search(line)]
    assert not hits, "מקור apt ברשת (הכרעת 15/10, #1411):\n" + "\n".join(hits)


def test_the_detector_catches_each_shape_of_live_source():
    """בקרה על הסורק עצמו: כל צורה של מקור חי נתפסת, וצורה לגיטימית לא."""
    for bad in ('NET_SOURCE="deb https://deb.debian.org/debian trixie main"',
                "deb http://security.debian.org/debian-security trixie-security main",
                'echo "deb [signed-by=/k.gpg] https://example.com/apt stable main" > x.list',
                "deb [trusted=yes] http://10.0.0.5/repo trixie main",
                "add-apt-repository ppa:x/y", "apt-key add k.asc"):
        assert NETWORK_APT.search(bad), bad
    for ok in ("deb [trusted=yes] file:/var/lib/imagesrv/apt-repo trixie main contrib",
               "deb [trusted=yes] file:/cdrom trixie main contrib non-free-firmware",
               "rm -f /etc/apt/sources.list.d/imagesrv-debian.list"):
        assert not NETWORK_APT.search(ok), ok
