"""השער שאומר אם תחנה יכולה בכלל לעלות דרך השרת שהותקן (#332).

בהתקנה נקייה מאפס המתקין יצא **0** והדפיס `מוכן.` בזמן ש-`/srv/imagesrv/boot`
הייתה ריקה, ושני הקבצים שתפריט ה-GRUB מפנה אליהם — ‏`/boot/vmlinuz`
ו-`/boot/initrd.img` — החזירו **404**. שרת מוכן שאף מחשב לא עולה דרכו.

הבדיקות כאן מריצות את `deploy/verify-boot-payload.sh` **בפועל**, מול שרת
‏HTTP אמיתי, ובודקות **קוד יציאה והודעה**. בדיקת נוכחות טקסט בסקריפט
הייתה עוברת גם על שער שאינו רץ.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from native import requires_native
from server.system.health import BOOT_ASSETS, MIN_ASSET_BYTES

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "verify-boot-payload.sh"
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
BASH = shutil.which("bash")

#: גוף ה-404 של ‎/boot — **תשעה** בייטים. זה המספר שהוגש ב-200 מדומה
#: ונספר כהצלחה, וזו הסיבה ש"יש תשובה ויש גודל" אינו "יש קובץ".
NOT_FOUND_BODY = b"Not Found"
GUI_INITRD = "initrd.img.gui"
GUI_MIN_ASSET_BYTES = 30 * 1024 * 1024

pytestmark = requires_native(
    "bash", "python3", why="השער הוא סקריפט bash שמריץ python3"
)


class _BootHandler(BaseHTTPRequestHandler):
    """מגיש את `/boot/<שם>` מתוך `root`, ומחזיר 404 קצר כמו השרת האמיתי."""

    root: Path

    def do_GET(self):  # noqa: N802 — שם מוכתב על ידי BaseHTTPRequestHandler
        name = self.path.rsplit("/", 1)[-1]
        path = self.root / name
        if not self.path.startswith("/boot/") or not path.is_file():
            body = NOT_FOUND_BODY
            self.send_response(404)
        else:
            body = path.read_bytes()
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serving(root: Path):
    """שרת אמיתי על פורט חופשי; מחזיר את הכתובת שהתחנה הייתה פונה אליה."""
    handler = type("_Bound", (_BootHandler,), {"root": root})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def dead_address() -> str:
    """כתובת שאיש אינו מאזין בה — שרת שנפל, לא שרת שהחזיר 404."""
    with serving(Path(".")) as base:
        return base


def run_gate(boot_dir: Path, base: str):
    """מריץ את השער עצמו. ‏--wait 0: השרת כבר למעלה או כבר לא יעלה.

    ‏`PYTHONIOENCODING`: היעד הוא דביאן 13 עם UTF-8, וכך גם המעבדה. קונסולת
    הפיתוח בווינדוס היא cp1252, ובלי הדגל ההודעות היו חוזרות כ-`\\uXXXX`
    ומודדות את הקונסולה במקום את השער.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [BASH, str(SCRIPT), "--app-dir", str(REPO), "--http-root", str(boot_dir),
         "--server-url", base, "--wait", "0"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )


def boot_dir(tmp_path: Path, size: int | None) -> Path:
    """תיקיית אתחול: ריקה כש-`size` הוא None, אחרת שני קבצים בגודל הזה."""
    directory = tmp_path / "boot"
    directory.mkdir()
    if size is not None:
        for name in BOOT_ASSETS:
            (directory / name).write_bytes(b"\0" * size)
    return directory


def test_an_empty_boot_directory_is_refused(tmp_path: Path):
    """המצב שנמדד בהתקנה הנקייה: התיקייה ריקה ושני הקבצים החזירו 404."""
    directory = boot_dir(tmp_path, None)
    with serving(directory) as base:
        result = run_gate(directory, base)

    assert result.returncode != 0, result.stdout + result.stderr
    for name in BOOT_ASSETS:
        assert name in result.stderr, f"{name} לא נאמר בשמו"
    assert "404" in result.stderr
    # ההודעה חייבת לומר גם **מה עושים**, לא רק מה חסר.
    assert "build_initramfs.sh" in result.stderr


def test_nine_bytes_served_with_200_is_not_success(tmp_path: Path):
    """‏200 וגודל אינם ראיה. תשעה בייטים הם גוף שגיאה, לא קרנל."""
    directory = boot_dir(tmp_path, len(NOT_FOUND_BODY))
    with serving(directory) as base:
        result = run_gate(directory, base)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "9 בייטים" in result.stderr, result.stderr


def test_files_on_disk_that_the_server_does_not_serve_are_refused(tmp_path: Path):
    """הקבצים על הדיסק, השרת מגיש תיקייה אחרת. ‏`ls` אינו אימות —
    התחנה מושכת ב-HTTP, וזה מה שנבדק."""
    directory = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with serving(elsewhere) as base:
        result = run_gate(directory, base)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "404" in result.stderr


def test_a_boot_directory_that_is_not_the_one_being_served_is_refused(tmp_path: Path):
    """‏200 וגודל מלא — ובכל זאת כישלון, כי התיקייה שנמסרה ריקה.

    ‏`--boot-dir` של השרת הוא `/srv/imagesrv/boot` (server/main.py), וזה
    גם ה-`--http-root` של המתקין. כששניהם אינם אותו נתיב, המפעיל מילא
    תיקייה שאיש אינו מגיש — ו-HTTP לבדו היה מאשר את זה.
    """
    served = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    empty = tmp_path / "empty"
    empty.mkdir()
    with serving(served) as base:
        result = run_gate(empty, base)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "אינו קיים" in result.stderr, result.stderr


def test_a_server_that_did_not_answer_is_a_failure_not_a_pass(tmp_path: Path):
    """‏"לא הצלחנו לבדוק" אינו "בדקנו, הכל תקין" — עיקרון 5."""
    directory = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    result = run_gate(directory, dead_address())

    assert result.returncode != 0, result.stdout + result.stderr
    assert "הבדיקה עצמה לא רצה" in result.stderr, result.stderr


def test_real_files_that_are_really_served_pass(tmp_path: Path):
    """הצד החיובי — בלעדיו השער היה "מסרב תמיד", וזה לא שער."""
    directory = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    with serving(directory) as base:
        result = run_gate(directory, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "תחנה יכולה לעלות" in result.stdout


def test_absent_gui_initrd_is_a_named_non_blocking_outcome(tmp_path: Path):
    """A server built without --with-gui falls back to text, but must say so."""
    directory = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    with serving(directory) as base:
        result = run_gate(directory, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{GUI_INITRD}: absent" in result.stdout


def test_empty_gui_initrd_is_refused(tmp_path: Path):
    """Negative control: a named GUI payload that is truncated cannot pass."""
    directory = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    (directory / GUI_INITRD).write_bytes(b"")
    with serving(directory) as base:
        result = run_gate(directory, base)

    assert result.returncode != 0, result.stdout + result.stderr
    assert GUI_INITRD in result.stderr
    assert str(GUI_MIN_ASSET_BYTES) in result.stderr


def gate_block() -> str:
    """קטע השער מתוך המתקין, כפי שהוא — מ-`READY=1` ועד סוף הקובץ.

    מריצים את הקוד האמיתי ולא בודקים נוכחות טקסט: שער שרץ **אחרי**
    "מוכן." או שאינו מפיל את קוד היציאה עובר כל בדיקת grep, ומשאיר את
    ‏#332 בדיוק כפי שנמדד.
    """
    lines = INSTALLER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line == "READY=1")
    return "\n".join(lines[start:])


def run_installer_tail(boot_dir: Path, base: str):
    """מריץ את קטע השער עם הסביבה שהמתקין היה נותן לו."""
    prelude = (
        "set -euo pipefail\n"
        "GRN=''; YEL=''; DIM=''; OFF=''\n"
        "say() { printf '==> %s\\n' \"$*\"; }\n"
        "DRY_RUN=0\n"
        f"APP_DIR={str(REPO)!r}\n"
        f"HTTP_ROOT={str(boot_dir)!r}\n"
        f"SERVER_URL={base!r}\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [BASH, "-c", prelude + gate_block()],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )


def test_the_installer_does_not_say_ready_when_no_station_can_boot(tmp_path: Path):
    """זה בדיוק מה שנמדד: יציאה 0 ו-"מוכן." על תיקייה ריקה (#332)."""
    directory = boot_dir(tmp_path, None)
    with serving(directory) as base:
        result = run_installer_tail(directory, base)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "מוכן." not in result.stdout, result.stdout


def test_the_installer_does_say_ready_once_a_station_can_boot(tmp_path: Path):
    """הצד החיובי — שער שמסרב תמיד אינו שער אלא מתג כבוי."""
    directory = boot_dir(tmp_path, MIN_ASSET_BYTES + 1)
    with serving(directory) as base:
        result = run_installer_tail(directory, base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "מוכן." in result.stdout, result.stdout


@pytest.mark.parametrize("name", BOOT_ASSETS)
def test_the_gate_checks_exactly_what_the_grub_menu_points_at(name: str):
    """‏boot/grub_menu.py מפנה לשני אלה. רשימה שנסחפת ממנו היא שער
    שמאשר שרת שלא ניתן לעלות דרכו."""
    menu = (REPO / "boot" / "grub_menu.py").read_text(encoding="utf-8")
    assert f"/boot/{name}" in menu
