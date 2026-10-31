"""‏#833: המוניטור ב-view-only חייב לשרוד לקוח ששולח קלט.

‏`monitor.sh` נותן `--input` לבנייה ולמשכפל (#1454); view-only נשאר מצב של monitor.c. שם
‏`agent/monitor.c` השאיר את ‏`kbdAddEvent`/`ptrAddEvent` כ-`NULL`,
‏ו-LibVNCServer קורא להם **בלי בדיקה** — הלקוח של הקונסולה שולח
‏PointerEvent מיד אחרי ה-handshake, והתהליך קפץ לכתובת 0 (‏`segfault at 0
ip 0`, ‏rc=139, נמדד על .118 ב-15/09). ה-supervisor הפעיל מחדש אחרי 5ש',
ולכן מהדפדפן זה נראה "פורט פתוח אבל החיבור נסגר".

מה שנבדק כאן הוא **ההתנהגות**, לא הקוד: בונים את הבינארי מול ספריית
‏libvncserver של המערכת, מריצים אותו על קובץ שמחליף את ‏`/dev/fb0`
‏(`--fb FILE --geometry WxH`), ולקוח RFB מינימלי מבצע handshake, מבקש
‏FramebufferUpdate, שולח ‏KeyEvent + PointerEvent, ומבקש שוב. התהליך חייב
להישאר חי ולהגיש את שתי התמונות. הראיה החיובית היא ה-FramebufferUpdate
**השני** — אחרי הקלט (עיקרון 5): "החיבור לא נסגר" לבדו אינו ראיה.

בקרה שלילית: על ‏`= NULL` (‏main ‏2ae4b11) הלקוח מקבל את התמונה הראשונה,
ואחרי ה-KeyEvent הסוקט נסגר — ‏`_recv_exact` נופל בשם, והתהליך יוצא
ב-SIGSEGV (‏rc=-11, ‏139 במעטפת). מה שנמדד על הברזל ב-15/09 הוא בדיוק
הרצף הזה; הבקרה עצמה רצה במעבדה, לא בווינדוס.

‏gcc + libvncserver-dev יש רק במעבדה (‏`build_initramfs.sh` מתקין אותם);
בווינדוס הבדיקה מדלגת בהצהרה — ‏`requires_native`, כמו ‏`test_cloner_gui`.

‏#839 — האימות: ‏5900 קשוב לכל וילן ההפצה, ולכן לחיצת-היד של ה-RFB היא
השער. המוניטור מציע **רק** סוג-אבטחה 2 (מסגור VNC Authentication); התשובה
ל-challenge היא 16 בייטי סוד-האתחול (`--secret-file`, 32 hex). לקוח בלי
הסוד לא מקבל מסך, לא מזריק קלט, ו**לא מפעיל reboot** — גם אם דחף
ClientCutText מיד אחרי התשובה השגויה. ההוכחה היא סטאב `reboot` ב-PATH
שכותב קובץ: בלי סוד הקובץ לא נוצר, עם סוד הוא נוצר.

בקרה שלילית (מעבדה, על `origin/main` שלפני #839, ‏f83eb4f): שם אין
`--secret-file` (הבינארי מסרב לדגל — משמיטים אותו), הרשימה מציעה `1`,
ו-`test_power_cut_text_needs_an_authenticated_session` נופל בשלב הראשון:
הקובץ **נוצר** מחיבור שלא הזדהה. זו בדיוק החולשה.
"""

from __future__ import annotations

import hashlib
import hmac as hmaclib
import os
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
MONITOR_C = REPO / "agent" / "monitor.c"
HMAC_C = REPO / "agent" / "hmac_sha256.c"

WIDTH, HEIGHT = 64, 32
SECRET = "00112233445566778899aabbccddeeff"
CHALLENGE = bytes(range(16))                     # אותו וקטור כמו test_monitor.py
KNOWN_HMAC = bytes.fromhex("a5ce9cbf7c63cbedf3403e594d04b0ef")

SECRET_BYTES = bytes.fromhex(SECRET)
WRONG_SECRET = bytes(16)
POWER_REBOOT = b"imagesrv-power:reboot"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("libvncserver", _pkgconfig("libvncserver")),
    posix=True,
    why="imagesrv-monitor נבנה על המעבדה בלבד",
)


def _hmac16(secret: bytes, challenge: bytes) -> bytes:
    return hmaclib.new(secret, challenge, hashlib.sha256).digest()[:16]


def _build(tmp_path: Path) -> Path:
    """אותה שורת הידור כמו ב-`tools/build_initramfs.sh`."""
    binary = tmp_path / "imagesrv-monitor"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(binary),
         str(MONITOR_C), str(HMAC_C), "-lvncserver"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    return binary


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """קורא בדיוק n בייטים; EOF מוקדם הוא כישלון בשם — לא bytes קצר."""
    chunks = bytearray()
    while len(chunks) < n:
        piece = sock.recv(n - len(chunks))
        assert piece, f"the monitor closed the socket after {len(chunks)}/{n} bytes"
        chunks += piece
    return bytes(chunks)


def _security_types(sock: socket.socket) -> bytes:
    """גרסה + רשימת סוגי האבטחה שהמוניטור מציע ללקוח 3.8."""
    banner = _recv_exact(sock, 12)
    assert banner.startswith(b"RFB 003."), banner
    sock.sendall(b"RFB 003.008\n")
    count = _recv_exact(sock, 1)[0]
    assert count, "the server offered no security types"
    return _recv_exact(sock, count)


def _answer_challenge(sock: socket.socket, response: bytes | None = None,
                      trailing: bytes = b"",
                      secret: bytes = SECRET_BYTES) -> int:
    """בוחר סוג 2, עונה ל-challenge ב-HMAC (או ב-`response` מפורש —
    סוד גולמי לבקרה שלילית), ומחזיר את SecurityResult."""
    sock.sendall(b"\x02")
    challenge = _recv_exact(sock, 16)
    assert len(challenge) == 16
    if response is None:
        response = _hmac16(secret, challenge)
    sock.sendall(response + trailing)
    return struct.unpack(">I", _recv_exact(sock, 4))[0]


#: ‏PIXEL_FORMAT של RFB: ‏bpp, depth, big-endian, true-colour, שלושה max,
#: שלושה shift, ‏3 ריפוד.
PIXEL_FORMAT = ">BBBBHHHBBB3x"


def _server_init(sock: socket.socket) -> tuple[int, int, tuple[int, ...]]:
    """‏RFB 3.8, סוג 2 עם HMAC-SHA256(סוד, challenge), ‏ClientInit משותף.
    מחזיר את גודל המסך ואת פורמט הפיקסלים שהשרת הצהיר עליו. ‏None אסור
    שיוצע בכלל — זה השער של #839."""
    types = _security_types(sock)
    assert 1 not in types, f"None security offered: {list(types)}"
    assert 2 in types, f"no secret security in {list(types)}"
    assert _answer_challenge(sock) == 0, "security failed"
    sock.sendall(b"\x01")                      # ClientInit: shared
    width, height = struct.unpack(">HH", _recv_exact(sock, 4))
    pixel_format = struct.unpack(PIXEL_FORMAT, _recv_exact(sock, 16))
    name_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
    _recv_exact(sock, name_len)
    return width, height, pixel_format


def _handshake(sock: socket.socket) -> tuple[int, int]:
    width, height, _ = _server_init(sock)
    return width, height


def _request_pixels(sock: socket.socket, width: int, height: int) -> bytes:
    """‏FramebufferUpdateRequest מלא (לא incremental), וקריאת ה-Update.
    מחזיר את בייטי הפיקסלים שהגיעו ב-Raw, בסדר המלבנים."""
    sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))
    kind = _recv_exact(sock, 1)[0]
    assert kind == 0, f"expected FramebufferUpdate (0), got message type {kind}"
    _recv_exact(sock, 1)
    rects = struct.unpack(">H", _recv_exact(sock, 2))[0]
    pixels = bytearray()
    for _ in range(rects):
        _x, _y, w, h, encoding = struct.unpack(">HHHHi", _recv_exact(sock, 12))
        assert encoding == 0, f"unexpected encoding {encoding}; the client asked for none"
        pixels += _recv_exact(sock, w * h * 4)
    return bytes(pixels)


def _request_update(sock: socket.socket, width: int, height: int) -> int:
    """כמה בייטי פיקסלים הגיעו ב-Raw — הראיה שהתמונה הוגשה."""
    return len(_request_pixels(sock, width, height))


def _set_pixel_format(sock: socket.socket, red: int, green: int, blue: int) -> None:
    """‏SetPixelFormat (msg 0): ‏32bpp, ‏depth 24, little-endian, true-colour,
    עם ה-shifts שהלקוח מבקש — כמו `setPixelFormat()` ב-monitor.js."""
    sock.sendall(struct.pack(">B3x" + PIXEL_FORMAT[1:], 0, 32, 24, 0, 1,
                             255, 255, 255, red, green, blue))


def _key(sock: socket.socket, keysym: int, down: bool) -> None:
    sock.sendall(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))


def test_shifted_number_keysyms_are_mapped_by_the_injector():
    """Shifted number symbols used to fall through and be silently dropped."""
    source = MONITOR_C.read_text(encoding="utf-8")
    shifted_digits = {
        ")": "KEY_0", "!": "KEY_1", "@": "KEY_2", "#": "KEY_3",
        "$": "KEY_4", "%": "KEY_5", "^": "KEY_6", "&": "KEY_7",
        "*": "KEY_8", "(": "KEY_9",
    }
    for symbol, code in shifted_digits.items():
        assert f"case '{symbol}': return {code};" in source


def _pointer(sock: socket.socket, buttons: int, x: int, y: int) -> None:
    sock.sendall(struct.pack(">BBHH", 5, buttons, x, y))


def _cut_text(text: bytes) -> bytes:
    """‏ClientCutText (msg 6), כמו ש-monitor.js בונה אותו."""
    return struct.pack(">BxxxI", 6, len(text)) + text


class Monitor:
    """מוניטור חי על קובץ-framebuffer, עם קובץ סוד, ולוג. ‏`env` מאפשר
    להחליף PATH — כך `reboot` הוא סטאב ולא המכונה."""

    def __init__(self, tmp_path: Path, binary: Path, *, secret: str | None = SECRET,
                 env: dict | None = None, extra: tuple[str, ...] = (),
                 allow_from: str | None = "127.0.0.1",
                 width: int = WIDTH, height: int = HEIGHT):
        fb = tmp_path / "fb0.raw"
        self.fb = fb
        fb.write_bytes(bytes([0x10, 0x20, 0x30, 0x00]) * (width * height))
        self.port = _free_port()
        self.log_path = tmp_path / "monitor.log"
        self.log = open(self.log_path, "wb")
        self.secret_file = tmp_path / "monitor.secret"
        argv = [str(binary), "--bind", "127.0.0.1", "--port", str(self.port),
                "--fps", "10", "--fb", str(fb), "--geometry", f"{width}x{height}"]
        if secret is not None:
            self.secret_file.write_text(secret + "\n")
            argv += ["--secret-file", str(self.secret_file)]
        if allow_from is not None:
            argv += ["--allow-from", allow_from]
        argv += list(extra)
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
            env=env,
        )

    def connect(self) -> socket.socket:
        deadline = time.monotonic() + 10
        while True:
            assert self.proc.poll() is None, (
                f"the monitor exited before listening, rc={self.proc.returncode}")
            try:
                return socket.create_connection(("127.0.0.1", self.port), timeout=5)
            except OSError:
                assert time.monotonic() < deadline, "no RFB listener within 10s"
                time.sleep(0.1)

    def alive(self) -> bool:
        time.sleep(0.3)
        return self.proc.poll() is None

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.log.close()
        return self.log_path.read_text(errors="replace")


@NATIVE
def test_view_only_monitor_survives_keyboard_and_pointer_events(tmp_path):
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    proc = monitor.proc
    try:
        sock = monitor.connect()
        with sock:
            width, height = _handshake(sock)
            assert (width, height) == (WIDTH, HEIGHT)

            # (א) ‏FramebufferUpdateRequest לבד — לא מפיל (השאלה הפתוחה ב-#833).
            assert _request_update(sock, width, height) == WIDTH * HEIGHT * 4

            # (ב) קלט, כמו שהלקוח של הקונסולה שולח: מקש ולחיצת עכבר.
            _key(sock, 0xFF0D, True)            # Enter down
            _key(sock, 0xFF0D, False)
            _pointer(sock, 1, 5, 5)             # left button down at (5,5)
            _pointer(sock, 0, 6, 6)

            # (ג) הראיה החיובית: התהליך עדיין מגיש תמונה **אחרי** הקלט.
            assert _request_update(sock, width, height) == WIDTH * HEIGHT * 4

        assert monitor.alive(), (
            f"the monitor died after client input, rc={proc.returncode}")
    finally:
        text = monitor.stop()
    assert "view-only mode; input disabled" in text, text


# --- #839: השער — לחיצת-היד, לא ה-WebSocket -----------------------------------


def _reboot_stub(tmp_path: Path) -> tuple[dict, Path]:
    """‏`reboot` מזויף ראשון ב-PATH: כותב את הארגומנטים שלו לקובץ. הקובץ
    הוא הראיה החיובית ש-run_power רץ — ולא-קיומו הוא הראיה שלא."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    marker = tmp_path / "reboot.called"
    (stubs / "reboot").write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$REBOOT_MARKER"\n')
    (stubs / "reboot").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{stubs}:{env.get('PATH', '')}"
    env["REBOOT_MARKER"] = str(marker)
    return env, marker


@NATIVE
def test_monitor_offers_only_the_secret_type_and_rejects_a_wrong_secret(tmp_path):
    """סוד שגוי → SecurityResult 1 + סיבה, והחיבור נסגר; בחירת None (1)
    → אין SecurityResult 0 בכלל. ואחרי שניהם המוניטור חי ומגיש מסך
    ללקוח עם הסוד הנכון — הדחייה אינה קריסה ואינה נעילה."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    try:
        with monitor.connect() as sock:
            types = _security_types(sock)
            assert 1 not in types and 2 in types, list(types)
            assert _answer_challenge(sock, SECRET_BYTES) == 1   # גולמי ≠ HMAC
            reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
            _recv_exact(sock, reason_len)
        assert monitor.alive()

        with monitor.connect() as sock:
            types = _security_types(sock)
            assert _answer_challenge(sock, WRONG_SECRET) == 1
            reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
            reason = _recv_exact(sock, reason_len)
            assert reason, "a 3.8 rejection carries a reason string"
            sock.settimeout(5)
            assert sock.recv(1) == b"", "the socket must close after a rejection"
        assert monitor.alive()

        with monitor.connect() as sock:
            _security_types(sock)
            sock.sendall(b"\x01")                  # None -- not offered
            sock.settimeout(5)
            head = b""
            while len(head) < 4:
                piece = sock.recv(4 - len(head))
                if not piece:
                    break
                head += piece
            # ‏3.8: ‏0 = "החיבור נכשל" + סיבה, או EOF. לעולם לא 0-אחרי-בחירה
            # ‏= הצלחה, כי ההצלחה היחידה היא SecurityResult אחרי challenge.
            assert head in (b"", b"\x00\x00\x00\x00"), head
            if head:
                reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
                assert reason_len, "connection-failed carries a reason"
        assert monitor.alive()

        with monitor.connect() as sock:
            width, height = _handshake(sock)
            assert _request_update(sock, width, height) == WIDTH * HEIGHT * 4
    finally:
        text = monitor.stop()
    assert "rejected client" in text, text


@NATIVE
def test_power_cut_text_needs_an_authenticated_session(tmp_path):
    """(א) לקוח עם סוד שגוי שדוחף ClientCutText באותה כתיבה → `reboot`
    לא רץ (אין קובץ). (ב) לקוח מאומת ששולח אותו טקסט → `reboot -f` רץ.
    (ב) הוא השומר מפני תיקון-יתר: השער דוחה זרים, לא את כולם."""
    binary = _build(tmp_path)
    env, marker = _reboot_stub(tmp_path)
    monitor = Monitor(tmp_path, binary, env=env)
    try:
        with monitor.connect() as sock:
            _security_types(sock)
            assert _answer_challenge(sock, WRONG_SECRET,
                                     trailing=_cut_text(POWER_REBOOT)) == 1
        time.sleep(1.0)
        assert not marker.exists(), (
            f"reboot ran for an unauthenticated client: {marker.read_text()}")
        assert monitor.alive()

        with monitor.connect() as sock:
            _handshake(sock)
            sock.sendall(_cut_text(POWER_REBOOT))
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        assert marker.exists(), "reboot did not run for the authenticated client"
        assert marker.read_text().split() == ["-f"]
        assert monitor.alive(), "run_power must not take the monitor down"
    finally:
        text = monitor.stop()
    assert "remote reboot requested" in text, text


@NATIVE
def test_monitor_refuses_to_start_without_a_usable_secret(tmp_path):
    """בלי `--secret-file` — usage ויציאה; קובץ שאינו 32 hex — יציאה עם
    הסיבה. בשני המקרים 5900 לא נפתח: פתיחה בלי אימות אינה ברירת מחדל."""
    binary = _build(tmp_path)
    fb = tmp_path / "fb0.raw"
    fb.write_bytes(bytes(WIDTH * HEIGHT * 4))
    base = [str(binary), "--bind", "127.0.0.1", "--port", str(_free_port()),
            "--fb", str(fb), "--geometry", f"{WIDTH}x{HEIGHT}",
            "--allow-from", "127.0.0.1"]

    without = subprocess.run(base, capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL)
    assert without.returncode != 0
    assert "--secret-file" in without.stderr, without.stderr

    bad = tmp_path / "bad.secret"
    bad.write_text("not-a-secret\n")
    malformed = subprocess.run(base + ["--secret-file", str(bad)],
                               capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
    assert malformed.returncode != 0
    assert "hex" in malformed.stderr, malformed.stderr


@NATIVE
def test_monitor_refuses_to_start_without_a_usable_allow_from(tmp_path):
    """#1077: ‏5900 נגיש רק לשרת, ולכן `--allow-from` חובה וכתובת IPv4 תקינה.
    בלי הדגל — usage ויציאה; כתובת לא תקינה — יציאה בשמה. עד כאן אף טסט לא
    הפיל את הסרת שני השומרים (בקרה רטרואקטיבית, 24/10): בלעדיהם המוניטור
    עולה עם ‏`allow_from` = ‏0.0.0.0 — שער שאיש לא קבע."""
    binary = _build(tmp_path)
    fb = tmp_path / "fb0.raw"
    fb.write_bytes(bytes(WIDTH * HEIGHT * 4))
    secret = tmp_path / "monitor.secret"
    secret.write_text(SECRET + "\n")
    base = [str(binary), "--bind", "127.0.0.1", "--port", str(_free_port()),
            "--fb", str(fb), "--geometry", f"{WIDTH}x{HEIGHT}",
            "--secret-file", str(secret)]

    def exit_of(argv):
        """(קוד יציאה, stderr) — או (None, …) כשהמוניטור עלה ועדיין רץ."""
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True)
        try:
            _, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
            return None, err
        return proc.returncode, err

    code, err = exit_of(base)
    assert code is not None, f"המוניטור עלה בלי --allow-from: {err}"
    assert code != 0 and "--allow-from" in err, (code, err)

    code, err = exit_of(base + ["--allow-from", "server.example"])
    assert code is not None, f"המוניטור עלה עם --allow-from לא תקין: {err}"
    assert code != 0 and "invalid --allow-from" in err, (code, err)


def test_view_only_source_never_assigns_null_input_callbacks():
    """שומר-מקור שרץ בכל תחנה, גם בלי gcc (ולכן בלי ‏NATIVE): ‏`kbdAddEvent`/`ptrAddEvent`
    לעולם אינם `NULL`. זה בדיוק השורש של #833 — ‏LibVNCServer אינו בודק.
    בקרה שלילית: החזרת ‏`screen->kbdAddEvent = NULL;` מפילה כאן."""
    import re
    source = MONITOR_C.read_text(encoding="utf-8")
    offenders = re.findall(r"(?:kbdAddEvent|ptrAddEvent)\s*=\s*NULL", source)
    assert offenders == [], offenders



# --- שומרי-מקור (#839) — רצים בכל תחנה, גם בלי gcc ---------------------------
#
# כמו `test_view_only_source_never_assigns_null_input_callbacks`: לא תחליף
# להרצה במעבדה, אלא רשת-ביטחון לרגרסיה שמישהו יעשה בווינדוס בלי לקמפל.


def _source() -> str:
    return MONITOR_C.read_text(encoding="utf-8")


def test_source_gates_rfb_on_the_secret_not_on_none():
    """‏authPasswdData לא-NULL + passwordCheck משלנו = None אינו מוצע.
    בקרה שלילית: מחיקת שתי השורות מחזירה את 5900 הפתוח."""
    c = _source()
    assert "screen->authPasswdData = secret;" in c
    assert "screen->passwordCheck = secret_check;" in c
    assert "--secret-file" in c
    assert "--allow-from" in c
    assert "hmac_sha256" in c
    assert "RFB_CLIENT_REFUSE" in c
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "hmac_sha256.c" in builder


def test_source_uses_hmac_not_the_raw_secret():
    """התשובה היא HMAC(secret, challenge), לא memcmp לסוד עצמו.
    בקרה שלילית: החזרת ההשוואה הגולמית מפילה את test_hmac למטה."""
    c = _source()
    assert "hmac_sha256(secret, SECRET_LEN" in c
    assert "authChallenge" in c
    assert "response[i] ^ secret[i]" not in c


def test_source_refuses_a_peer_that_is_not_the_server():
    c = _source()
    assert "peer_is_server" in c
    assert "getpeername" in c
    assert "not the server" in c


def test_source_never_exits_from_a_client_callback():
    """‏emit_event רץ מתוך callback של LibVNCServer; ‏fatal() שם היה
    קריסה מהצפת אירועים."""
    c = _source()
    assert 'fatal("write /dev/uinput")' not in c


def test_source_cleans_clients_before_destroying_uinput():
    c = _source()
    assert c.index("rfbScreenCleanup(screen);") < c.index("UI_DEV_DESTROY);")


def test_source_releases_keys_when_a_client_goes_and_reaps_power_children():
    c = _source()
    assert "clientGoneHook = client_gone" in c
    assert "signal(SIGCHLD, SIG_IGN)" in c


CC_ONLY = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    why="gcc for hmac_sha256.c unit test",
)


@CC_ONLY
def test_hmac_sha256_c_matches_the_known_vector(tmp_path):
    """ה-C והפייתון מסכימים על אותו וקטור. בלי libvncserver — רק hmac_sha256.c."""
    known = "a5ce9cbf7c63cbedf3403e594d04b0ef"

    driver = tmp_path / "hmac_drv.c"
    driver.write_text(
        '#include "hmac_sha256.h"\n'
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    unsigned char key[16] = {"
        + ",".join(str(b) for b in bytes.fromhex(SECRET))
        + "};\n"
        "    unsigned char msg[16] = {"
        + ",".join(str(b) for b in CHALLENGE)
        + "};\n"
        "    unsigned char out[32];\n"
        "    int i;\n"
        "    hmac_sha256(key, 16, msg, 16, out);\n"
        "    for (i = 0; i < 16; i++) printf(\"%02x\", out[i]);\n"
        "    printf(\"\\n\");\n"
        "    return 0;\n"
        "}\n"
    )
    binary = tmp_path / "hmac_drv"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(binary),
         str(driver), str(HMAC_C), "-I", str(REPO / "agent")],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True,
                         timeout=10, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == known


@NATIVE
def test_wrong_peer_is_closed_before_the_handshake(tmp_path):
    """peer שאינו --allow-from נסגר מיד. בקרה שלילית: מחיקת RFB_CLIENT_REFUSE
    נותנת באנר RFB לזר."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, allow_from="10.0.0.1")
    try:
        sock = monitor.connect()
        sock.settimeout(3)
        with sock:
            try:
                sock.recv(64)
            except OSError:
                pass
        assert monitor.alive()
    finally:
        text = monitor.stop()
    assert "not the server" in text, text


def _rekey(secret: bytes, challenge: bytes) -> bytes:
    """‏#1837: ‏`rekey_secret` — כתוב כאן בנפרד מ-monitor.c ומהשרת."""
    return hmaclib.new(secret, b"imagesrv-monitor-rekey" + challenge,
                       hashlib.sha256).digest()[:16]


@NATIVE
def test_authentication_moves_the_secret_to_the_derived_one(tmp_path):
    """‏#1837: האימות מעביר את הסוד — בקובץ ובזיכרון — ל-HMAC(סוד,
    תווית‖challenge), מה שהשרת גוזר מאותו challenge. ראיה חיובית: הקובץ
    שווה בדיוק לגזירה, הסוד הבא מתקבל, והסוד הקודם נדחה. עד #1837 הוגרל
    סוד אקראי ביציאת הלקוח, ואיש מחוץ למכונה לא יכול היה לדעת אותו."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    try:
        with monitor.connect() as sock:
            _security_types(sock)
            sock.sendall(b"\x02")
            challenge = _recv_exact(sock, 16)
            sock.sendall(_hmac16(SECRET_BYTES, challenge))
            assert struct.unpack(">I", _recv_exact(sock, 4))[0] == 0
            following = _rekey(SECRET_BYTES, challenge)
            assert monitor.secret_file.read_text().strip() == following.hex()

        with monitor.connect() as sock:                # הסוד הקודם — נדחה
            _security_types(sock)
            assert _answer_challenge(sock, secret=SECRET_BYTES) == 1
        with monitor.connect() as sock:                # הסוד הבא — מתקבל מיד
            _security_types(sock)
            assert _answer_challenge(sock, secret=following) == 0
        assert monitor.alive()
    finally:
        text = monitor.stop()
    assert "rekeyed session secret" in text, text


@NATIVE
def test_the_server_reconnects_to_the_real_monitor_without_a_hello(server, tmp_path):
    """‏#1837 מקצה לקצה: הפרוקסי של השרת (‏`create_monitor_router`) מול
    הבינארי האמיתי של הסוכן. hello אחד, ואז שלושה חיבורים ברצף — כמו מעבר
    מהיר בין מכונות בדף המוניטור. כל אחד מקבל ServerInit בניסיון הראשון.
    על הקוד שלפני #1837 השני נסגר 4512 (‏"password check failed!")."""
    import asyncio

    from starlette.websockets import WebSocketDisconnect

    from server.db import net_seen
    from test_monitor import MAC, browser_handshake, monitor_client, stored_secret

    binary = _build(tmp_path)
    machine = Monitor(tmp_path, binary)
    try:
        assert server["admin"].post(
            "/api/console/machines",
            json={"mac": MAC, "name": "Monitor", "group_id": "grp_BUILD"},
        ).status_code == 200
        net_seen(server["ctx"].conn, MAC, "127.0.0.1", monitor_secret=SECRET,
                 monitor_auth="hmac")                  # ה-hello היחיד

        async def connector(host, _port):
            return await asyncio.open_connection(host, machine.port)

        client = monitor_client(server, "admin", connector)
        for attempt in range(3):
            # ‏4409 = המנעול של החיבור הקודם לא שוחרר. נמדד 20/10: 5 מ-15 ריצות,
            # ונשאר לעולם — זה היה #1860 (ביטול בתוך wait_closed), שתוקף בנפרד;
            # הניסיון החוזר נשאר כמו ב-monitor.js. ‏4512 — הבאג של #1837 —
            # לעולם אינו מנוסה שוב: הוא נכשל כאן מיד.
            for _ in range(50):
                try:
                    with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
                        browser_handshake(ws)
                        head = b""
                        while len(head) < 4:
                            head += ws.receive_bytes()
                        assert struct.unpack(">HH", head[:4]) == (WIDTH, HEIGHT), attempt
                    break
                except WebSocketDisconnect as exc:
                    assert exc.code == 4409, (attempt, exc.code, exc.reason)
                    time.sleep(0.1)
            else:
                raise AssertionError(f"attempt {attempt}: still 4409 after 5s")
        assert stored_secret(server) == machine.secret_file.read_text().strip()
    finally:
        text = machine.stop()
    assert "bad HMAC" not in text, text


# --- ‏#949: מצביע מוחלט -----------------------------------------------------
#
# הדפדפן שולח PointerEvent עם מיקום **מוחלט** בפריימבאפר (‏`monitor.js`,
# ‏`fbCoords`). עד #949 ‏`monitor.c` הפך אותו לתזוזה יחסית (‏EV_REL) על התקן
# "עכבר", והסמן של הגואי — שמצטבר בנפרד, עם gain וגבולות משלו — סטה ממנו:
# "העכבר קופץ" (נדב, על מחשב הבנייה, 17/09). התיקון: ההתקן מצהיר ABS_X/ABS_Y
# בטווח 0..w-1 / 0..h-1, וכל PointerEvent (x,y) נכתב כ-ABS_X=x, ABS_Y=y.
#
# הראיה כאן היא ההתקן **בקרנל**, לא הקוד: קוראים את `/dev/input/eventN`
# שהמוניטור יצר, שואלים אותו (‏EVIOCGABS) על הטווח שהצהיר, ומקבלים ממנו את
# האירועים שהמוניטור הזריק. דורש `/dev/uinput` כתיב — כלומר root במעבדה.

import evdev_lab

UINPUT_NAME = "ImageSrv remote monitor"

WITH_UINPUT = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("libvncserver", _pkgconfig("libvncserver")),
    ("/dev/uinput (root)", evdev_lab.uinput_available()),
    posix=True,
    why="uinput של המוניטור נבדק במעבדה בלבד, כ-root",
)


def _open_monitor_input_device() -> int:
    node = evdev_lab.find_event_device(UINPUT_NAME)
    return os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)


@pytest.mark.uinput
@WITH_UINPUT
def test_input_device_is_an_absolute_pointer_over_the_framebuffer(tmp_path):
    """ההתקן שנוצר עם `--input` הוא ABS, לא REL, והטווח שלו הוא מידות ה-fb.
    בקרה שלילית: על main שלפני #949 ההתקן מצהיר EV_REL ואין לו EV_ABS."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, extra=("--input",))
    try:
        monitor.connect().close()                  # מאזין = ההתקן כבר נוצר
        fd = _open_monitor_input_device()
        try:
            assert evdev_lab.has_bit(fd, 0, evdev_lab.EV_ABS), "no EV_ABS on the device"
            assert not evdev_lab.has_bit(fd, 0, evdev_lab.EV_REL), "still a relative device"
            assert evdev_lab.has_bit(fd, evdev_lab.EV_ABS, evdev_lab.ABS_X)
            assert evdev_lab.has_bit(fd, evdev_lab.EV_ABS, evdev_lab.ABS_Y)
            assert evdev_lab.has_bit(fd, evdev_lab.EV_KEY, evdev_lab.BTN_LEFT)
            # לא מסך מגע: native-gui מצייר סמן רק להתקן מוחלט בלי BTN_TOUCH.
            assert not evdev_lab.has_bit(fd, evdev_lab.EV_KEY, evdev_lab.BTN_TOUCH)
            _, min_x, max_x = evdev_lab.absinfo(fd, evdev_lab.ABS_X)
            _, min_y, max_y = evdev_lab.absinfo(fd, evdev_lab.ABS_Y)
            assert (min_x, max_x) == (0, WIDTH - 1), (min_x, max_x)
            assert (min_y, max_y) == (0, HEIGHT - 1), (min_y, max_y)
        finally:
            os.close(fd)
    finally:
        text = monitor.stop()
    assert "input enabled through" in text, text


@pytest.mark.uinput
@WITH_UINPUT
def test_pointer_event_lands_as_the_same_absolute_position(tmp_path):
    """‏PointerEvent ב-(x,y) → ‏`EV_ABS ABS_X=x, ABS_Y=y` על ההתקן, וכפתור
    שמאלי → ‏BTN_LEFT; מיקום מעבר לקצה נצמד לפיקסל האחרון (#850). בקרה
    שלילית: הקוד הישן מזריק EV_REL (הפרשים) ואף ABS_X אינו מגיע."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, extra=("--input",))
    try:
        sock = monitor.connect()
        fd = _open_monitor_input_device()           # לפני הקלט: evdev אינו שומר לקורא שטרם פתח
        try:
            with sock:
                width, height = _handshake(sock)
                assert (width, height) == (WIDTH, HEIGHT)
                _pointer(sock, 0, 17, 9)
                _pointer(sock, 1, 40, 20)           # לחיצה שמאלית ב-(40,20)
                _pointer(sock, 0, WIDTH + 5, HEIGHT + 5)
                events = evdev_lab.read_events(fd, timeout=2.0)
        finally:
            os.close(fd)
    finally:
        text = monitor.stop()

    EV_ABS, EV_KEY, EV_REL = evdev_lab.EV_ABS, evdev_lab.EV_KEY, evdev_lab.EV_REL
    ABS_X, ABS_Y, BTN_LEFT = evdev_lab.ABS_X, evdev_lab.ABS_Y, evdev_lab.BTN_LEFT
    relative = [e for e in events if e[0] == EV_REL]
    assert relative == [], f"relative motion injected: {relative}"
    positions = [(c, v) for t, c, v in events if t == EV_ABS]
    assert positions == [
        (ABS_X, 17), (ABS_Y, 9),
        (ABS_X, 40), (ABS_Y, 20),
        (ABS_X, WIDTH - 1), (ABS_Y, HEIGHT - 1),
    ], f"events: {events}\nlog: {text}"
    buttons = [(c, v) for t, c, v in events if t == EV_KEY]
    assert buttons == [(BTN_LEFT, 1), (BTN_LEFT, 0)], buttons


# --- ‏#850: פורמט הפיקסלים וגבולות ה-mmap ---------------------------------------
#
# ‏(1) הפורמט נגזר מה-framebuffer ולא מונח. ‏LibVNCServer מצהיר כברירת מחדל
# על אדום בביט 0 (סדר R,G,B,X בזיכרון, ב-little-endian); ‏fbdev ‏XRGB8888
# ו-framebuffer הזיכרון (‏interfaces.md §15) מחזיקים אדום בביט 16. עד #850
# ‏monitor.c לא העתיק את ה-offsets ל-`serverFormat`, ולכן כל לקוח קיבל
# פיקסלים "מתורגמים" מפורמט שאינו שלהם. הראיה כאן היא מה שהלקוח מקבל:
# ‏ה-ServerInit, ובייטי הפיקסל עצמם בשני פורמטי לקוח שונים.
#
# ‏(2) כל בייט שלולאת ההעתקה קוראת נמצא בתוך המיפוי, והחשבון ב-64 ביט.
# גיאומטריה שגלשה ב-‏`__u32` עברה את בדיקת הגודל עם קובץ זעיר.

#: ‏Monitor כותב את הקובץ כ-`10 20 30 00` לכל פיקסל: ב-XRGB8888 LE זה
#: ‏B=0x10, ‏G=0x20, ‏R=0x30.
FIXTURE_BGRX = bytes([0x10, 0x20, 0x30, 0x00])


@NATIVE
def test_pixel_format_is_the_framebuffers_own_xrgb8888(tmp_path):
    """‏ServerInit מצהיר R@16 G@8 B@0; לקוח שמבקש את אותו פורמט (monitor.js)
    מקבל את הבייטים כמות שהם, ולקוח שמבקש R@0 מקבל אותם מתורגמים — כלומר
    השרת מתרגם **מהפורמט שהצהיר**, וההצהרה נכונה. מ-#1221 כל הפריים,
    כולל הפינה, נבדק בלי היתר — הסמן של LibVNCServer מבוטל.
    בקרה שלילית: על main שלפני #850 ה-ServerInit אומר R@0 G@8 B@16;
    בלי #1221 (`screen->cursor = NULL`) הפינה נכשלת על סמן ה-X."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    try:
        with monitor.connect() as sock:
            width, height, fmt = _server_init(sock)
            bpp, depth, big_endian, true_colour, r_max, g_max, b_max, r, g, b = fmt
            assert (r, g, b) == (16, 8, 0), f"ServerInit shifts R@{r} G@{g} B@{b}"
            # ‏true-colour הוא "nonzero" ב-RFB, ו-LibVNCServer שולח 0xFF
            # ‏(TRUE מוגדר -1 ב-rfbproto.h). ‏depth הוא ברירת המחדל של
            # ‏rfbGetScreen, ‏8*bytesPerPixel = 32 (נמדד ב-Testrunner עם #850,
            # ‏24/09) — לא נגענו בו; הלקוח מפענח לפי max/shift, לא לפי depth.
            assert (bpp, big_endian) == (32, 0), fmt
            assert true_colour != 0, fmt
            assert 24 <= depth <= 32, fmt
            assert (r_max, g_max, b_max) == (255, 255, 255), fmt

            # שלוש דגימות, כל אחת עם תקרה, כדי שריצה אחת תכריע בין
            # "העדכון הראשון יצא לפני ההעתקה" (תזמון, הטסט) לבין
            # "ההעתקה או התרגום מחזירים אפס" (הקוד). ‏native = בלי
            # SetPixelFormat, כלומר בפורמט השרת, בלי תרגום ב-LibVNCServer.
            seen = {}
            seen["native"] = _pixels_until_content(sock, width, height)
            _set_pixel_format(sock, 16, 8, 0)          # monitor.js: B,G,R,X
            seen["monitor.js"] = _pixels_until_content(sock, width, height)
            _set_pixel_format(sock, 0, 8, 16)          # R,G,B,X
            seen["rgbx"] = _pixels_until_content(sock, width, height)
    finally:
        log = monitor.stop()
    expected = {"native": FIXTURE_BGRX[:3], "monitor.js": FIXTURE_BGRX[:3],
                "rgbx": bytes([0x30, 0x20, 0x10])}
    report = {k: (tries, px[:4].hex(), sum(1 for x in px if x)) for k, (tries, px) in seen.items()}
    detail = f"(requests, first pixel, non-zero bytes): {report}\nlog: {log[-1500:]}"
    for name, (_, pixels) in seen.items():
        wrong = _compare_frame(pixels, width, height, expected[name])
        assert wrong == [], f"{name}: {len(wrong)} pixels differ, first {wrong[:5]}\n{detail}"


#: עד #1221 ‏LibVNCServer צייר לתוך הפריים את סמן ברירת המחדל שלו (‏X
#: בגודל 8×7, hotspot ‏(3,3), שחור על לבן) ללקוח שלא הצהיר על קידודי
#: סמן — הלקוח כאן, וגם monitor.js, מבקשים Raw/CopyRect/DesktopSize
#: בלבד. ‏monitor.c מחליף את ptrAddEvent, ולכן הסמן נשאר ב-(0,0). נמדד
#: ב-Testrunner 24/09: ‏6132 = 6144 − 4 פיקסלים שחורים × 3, בדיוק החלק
#: הנראה של הסמן. #1221 מבטל את הסמן (`screen->cursor = NULL`) —
#: אין עוד היתר לפינה, כל הפריים חייב להיות צבע הפיקצ'ר.


def _compare_frame(pixels: bytes, width: int, height: int,
                   expected: bytes) -> list:
    """כל פיקסל, כולל הפינה, חייב להיות `expected` (שלושת בייטי הצבע);
    מחזיר את הפיקסלים השגויים."""
    assert len(pixels) == width * height * 4, len(pixels)
    wrong = []
    for y in range(height):
        for x in range(width):
            at = (y * width + x) * 4
            colour = pixels[at:at + 3]
            if colour != expected:
                wrong.append((x, y, colour.hex()))
    return wrong


def _pixels_until_content(sock: socket.socket, width: int, height: int,
                          ceiling: float = 3.0) -> tuple[int, bytes]:
    """מבקש עדכון מלא שוב ושוב עד שמגיע פיקסל שאינו אפס, או עד התקרה.
    מחזיר (כמה בקשות, הבייטים האחרונים) — לא נופל בעצמו: הדוח של הטסט
    הוא שמכריע, והמספר הוא חלק מהראיה."""
    deadline = time.monotonic() + ceiling
    tries = 0
    while True:
        tries += 1
        pixels = _request_pixels(sock, width, height)
        if any(pixels) or time.monotonic() >= deadline:
            return tries, pixels
        time.sleep(0.2)


def _run_refused(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    """מריץ מוניטור שחייב לסרב לעלות. אם הוא עדיין רץ אחרי `timeout` —
    הוא עלה והגיש, וזה הכישלון (לא "עבר כי לא נפל"). הפלט לקבצים, לא
    ל-PIPE: תהליך שעלה וכותב לוג לא ייחסם על צינור מלא."""
    out_path = Path(argv[0]).with_suffix(".refused.log")
    with open(out_path, "wb") as log:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            pytest.fail("the monitor started instead of refusing: "
                        + out_path.read_text(errors="replace")[-2000:])
    return subprocess.CompletedProcess(argv, proc.returncode, "",
                                       out_path.read_text(errors="replace"))


def _base_argv(binary: Path, tmp_path: Path) -> list[str]:
    secret = tmp_path / "monitor.secret"
    secret.write_text(SECRET + "\n")
    return [str(binary), "--bind", "127.0.0.1", "--port", str(_free_port()),
            "--secret-file", str(secret), "--allow-from", "127.0.0.1"]


@NATIVE
def test_geometry_that_wraps_u32_is_refused_by_name(tmp_path):
    """‏65535×16385×4 = 4,295,163,900 — ב-`__u32` זה 196,604. קובץ בגודל
    הזה עבר את בדיקת הגודל, ולולאת ההעתקה קראה שורה של 262,140 בייט
    ממיפוי של 196,604. עכשיו: סירוב בשם, עם המספר האמיתי, לפני האזנה.
    בקרה שלילית: על main שלפני #850 התהליך ממשיך — ‏SIGSEGV בהעתקה או
    ‏FATAL על הקצאת 4GB — ואף אחד מהם אינו הסירוב הזה."""
    binary = _build(tmp_path)
    fb = tmp_path / "wrapped.raw"
    fb.write_bytes(bytes(196_604))
    run = _run_refused(_base_argv(binary, tmp_path) +
                       ["--fb", str(fb), "--geometry", "65535x16385"])
    assert run.returncode == 1, (run.returncode, run.stderr)
    assert "geometry needs 4295163900" in run.stderr, run.stderr
    assert "RFB listening" not in run.stderr, run.stderr


@NATIVE
def test_size_past_rfb_u16_is_refused_by_name(tmp_path):
    """‏ServerInit נושא רוחב וגובה כ-U16: מסך ברוחב 70000 היה מוצהר כ-4464.
    בקרה שלילית: על main שלפני #850 המוניטור עולה ומגיש (‏_run_refused
    נופל על "started instead of refusing")."""
    binary = _build(tmp_path)
    fb = tmp_path / "wide.raw"
    fb.write_bytes(bytes(70_000 * 4))
    run = _run_refused(_base_argv(binary, tmp_path) +
                       ["--fb", str(fb), "--geometry", "70000x1"])
    assert run.returncode == 1, (run.returncode, run.stderr)
    assert "layout out of bounds" in run.stderr and "RFB U16" in run.stderr, run.stderr


@NATIVE
def test_memfb_header_whose_row_wraps_u32_is_refused(tmp_path):
    """כותרת IMCTLFB1 עם width=0x40000001 ו-stride=4: ‏`width*4` ב-u32 הוא 4,
    ולכן `stride < width*4` לא תפס. בקרה שלילית: על main שלפני #850 הכותרת
    מתקבלת ("serving a memory framebuffer")."""
    binary = _build(tmp_path)
    fb = tmp_path / "fb.mem"
    header = b"IMCTLFB1" + struct.pack("<5I", 0x40000001, 1, 4, 1, 0)
    fb.write_bytes(header.ljust(4096, b"\0") + bytes(4))
    run = _run_refused(_base_argv(binary, tmp_path) + ["--fb", str(fb)])
    assert run.returncode == 1, (run.returncode, run.stderr)
    assert "not a memory framebuffer" in run.stderr, run.stderr
    assert "serving a memory framebuffer" not in run.stderr, run.stderr


# ‏/dev/fb0 עצמו — ‏yoffset, ‏xoffset, ‏offsets של ערוצים — אינו בר-זיוף בקובץ
# (אין ‏FBIOGET_* על קובץ רגיל). לכן שתי הפונקציות שמחליטות נבדקות ישירות:
# ‏driver שמכליל את monitor.c (‏main שלו בשם אחר) וקורא להן עם פריסות
# מזויפות. ‏revert מלא של monitor.c הוא כאן שגיאת הידור, לא בקרה —
# הבקרה ההתנהגותית לדרייבר היא מוטציה ממוקדת (ראו גוף ה-PR).

_DRIVER = r"""
#define main imagesrv_monitor_main
#include "monitor.c"
#undef main

static void fmt(const char *name, unsigned bpp, unsigned r, unsigned g,
                unsigned b, unsigned len, unsigned msb_right) {
    struct fb_var_screeninfo v;
    const char *why;
    memset(&v, 0, sizeof(v));
    v.bits_per_pixel = bpp;
    v.red.offset = r; v.green.offset = g; v.blue.offset = b;
    v.red.length = v.green.length = v.blue.length = len;
    v.red.msb_right = msb_right;
    why = pixel_format_error(&v);
    printf("%s=%s\n", name, why ? why : "OK");
}

static void lay(const char *name, unsigned xres, unsigned yres, unsigned xoff,
                unsigned yoff, unsigned stride, unsigned smem, unsigned header) {
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;
    const char *why;
    memset(&v, 0, sizeof(v));
    memset(&f, 0, sizeof(f));
    v.xres = xres; v.yres = yres; v.xoffset = xoff; v.yoffset = yoff;
    f.line_length = stride; f.smem_len = smem;
    why = layout_error(&v, &f, header);
    printf("%s=%s\n", name, why ? why : "OK");
}

int main(void) {
    fmt("xrgb", 32, 16, 8, 0, 8, 0);
    fmt("xbgr", 32, 0, 8, 16, 8, 0);
    fmt("rgbx", 32, 24, 16, 8, 8, 0);
    fmt("rgb565", 16, 11, 5, 0, 8, 0);
    fmt("nibble", 32, 20, 8, 0, 8, 0);
    fmt("shared", 32, 16, 16, 0, 8, 0);
    fmt("len6", 32, 16, 8, 0, 6, 0);
    fmt("msbright", 32, 16, 8, 0, 8, 1);
    lay("single", 1024, 768, 0, 0, 4096, 768 * 4096, 0);
    lay("panned", 1024, 768, 0, 768, 4096, 2 * 768 * 4096, 0);
    lay("panned_past", 1024, 768, 0, 768, 4096, 768 * 4096, 0);
    lay("tail_exact", 1000, 768, 0, 0, 4096, 767 * 4096 + 4000, 0);
    lay("tail_short", 1000, 768, 0, 0, 4096, 767 * 4096 + 3999, 0);
    lay("xoff_past", 1024, 768, 1, 0, 4096, 2 * 768 * 4096, 0);
    lay("wrap", 0x40000001u, 1, 0, 0, 4, 4096, 0);
    lay("memfb", 1280, 800, 0, 0, 5120, 4096 + 5120 * 800, 4096);
    lay("memfb_short", 1280, 800, 0, 0, 5120, 4096 + 5120 * 800 - 1, 4096);
    return 0;
}
"""


@NATIVE
def test_format_and_layout_checks_on_fake_fbdev_geometry(tmp_path):
    """‏(א) פורמט: כל סידור של שלושה ערוצי 8 ביט על גבולות בייט מתקבל;
    ‏16bpp, ‏offset שאינו כפולה של 8, ערוצים חופפים, ‏length≠8, ‏msb_right —
    נדחים, כל אחד בשם. ‏(ב) גבולות: ‏double-buffer ב-yoffset=yres מתקבל כשה-
    ‏smem_len מכיל אותו ונדחה כשלא — זה בדיוק תרחיש ה-Issue; הזנב המדויק
    (שורה אחרונה בלי ריפוד) מתקבל ובייט אחד פחות נדחה — הבדיקה אינה
    מחמירה מהקריאה עצמה."""
    driver = tmp_path / "layout_drv.c"
    driver.write_text(_DRIVER)
    binary = tmp_path / "layout_drv"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(binary), str(driver),
         str(HMAC_C), "-I", str(REPO / "agent"), "-lvncserver"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True,
                         timeout=10, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    got = dict(line.split("=", 1) for line in run.stdout.splitlines())

    for accepted in ("xrgb", "xbgr", "rgbx", "single", "panned", "tail_exact", "memfb"):
        assert got[accepted] == "OK", (accepted, got[accepted])
    assert "32 bpp" in got["rgb565"], got
    assert "byte boundary" in got["nibble"], got
    assert "share a byte" in got["shared"], got
    assert "8 bits" in got["len6"] and "8 bits" in got["msbright"], got
    assert "mapped length" in got["panned_past"], got
    assert "mapped length" in got["tail_short"], got
    assert "mapped length" in got["memfb_short"], got
    assert "stride" in got["xoff_past"], got
    assert "RFB U16" in got["wrap"], got


def test_source_serves_the_framebuffers_own_format_and_bounds():
    """שומר-מקור (רץ גם בלי gcc): ה-shifts מועתקים מה-fb, ‏layout_error
    נקרא לפני ה-mmap, והקורא של ה-seqlock מגודר לפני הבדיקה החוזרת.
    בקרה שלילית: על main שלפני #850 אף אחת מהמחרוזות אינה בקובץ."""
    c = _source()
    assert "serverFormat.redShift = (uint8_t)var.red.offset" in c
    assert "serverFormat.blueShift = (uint8_t)var.blue.offset" in c
    assert c.index("layout_error(&var, &fix, pixel_offset)") < c.index("mmap(NULL")
    assert c.index("__atomic_thread_fence(__ATOMIC_ACQUIRE)") < c.rindex(
        "__atomic_load_n(frame_seq, __ATOMIC_ACQUIRE)")


# --- ‏#1475: רק מה שהשתנה יוצא לרשת -------------------------------------------
#
# עד #1475 כל טיק העתיק את כל ה-fb וסימן את **כל** המסך כמשתנה, והלקוח
# (Raw בלבד) קיבל 1920×1080×4 = 8.3MB בכל עדכון — גם כשרק הסמן זז. זה
# היה רוב הקופצניות של הסמן במוניטור (נדב, משכפל 2, 16/10). עכשיו הסריקה
# משווה אריחים של 64×32 ומסמנת רק את מה שהשתנה.

TILE_W, TILE_H = 64, 32


def _read_update(sock: socket.socket) -> list[tuple[int, int, int, int, bytes]]:
    """‏FramebufferUpdate אחד: רשימת (x, y, w, h, פיקסלים) של מלבני Raw."""
    kind = _recv_exact(sock, 1)[0]
    assert kind == 0, f"expected FramebufferUpdate (0), got message type {kind}"
    _recv_exact(sock, 1)
    count = struct.unpack(">H", _recv_exact(sock, 2))[0]
    rects = []
    for _ in range(count):
        x, y, w, h, encoding = struct.unpack(">HHHHi", _recv_exact(sock, 12))
        assert encoding == 0, f"unexpected encoding {encoding}"
        rects.append((x, y, w, h, _recv_exact(sock, w * h * 4)))
    return rects


@NATIVE
def test_only_the_changed_tile_is_sent_and_an_idle_screen_sends_nothing(tmp_path):
    """מסך 256×128 (‏4×4 אריחים). אחרי העדכון המלא: (א) בקשה incremental בלי
    שום שינוי — **שום** עדכון בתוך 0.8 שנ' (שמונה טיקים ב-10fps); (ב) שינוי
    של 2×2 פיקסלים ב-(200,100) — אותה בקשה נענית במלבן אחד, האריח
    (192,96) 64×32, עם הפיקסלים החדשים. הראיה החיובית היא (ב): השתיקה
    ב-(א) אינה "החיבור מת".
    בקרה שלילית: על הקוד שלפני #1475 (‏`rfbMarkRectAsModified(screen, 0, 0,
    var.xres, var.yres)` בכל טיק) — (א) מקבל את כל המסך, ‏256×128, תוך טיק."""
    width, height = 256, 128
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, width=width, height=height)
    try:
        with monitor.connect() as sock:
            assert _handshake(sock) == (width, height)
            assert _request_update(sock, width, height) == width * height * 4

            sock.sendall(struct.pack(">BBHHHH", 3, 1, 0, 0, width, height))
            sock.settimeout(0.8)
            try:
                early = sock.recv(1)
            except socket.timeout:
                early = b""
            assert early == b"", "an unchanged screen still produced an update"

            patch = bytes([0xAA, 0xBB, 0xCC, 0x00]) * 2
            with open(monitor.fb, "r+b") as fb:
                for row in (100, 101):
                    fb.seek((row * width + 200) * 4)
                    fb.write(patch)
            sock.settimeout(5)
            rects = _read_update(sock)
    finally:
        log = monitor.stop()
    assert [(x, y, w, h) for x, y, w, h, _ in rects] == [(192, 96, TILE_W, TILE_H)], (
        f"rects: {[(x, y, w, h) for x, y, w, h, _ in rects]}\nlog: {log[-1500:]}")
    pixels = rects[0][4]
    at = ((100 - 96) * TILE_W + (200 - 192)) * 4
    assert pixels[at:at + 3] == bytes([0xAA, 0xBB, 0xCC]), pixels[at:at + 4].hex()
    assert pixels[:3] == FIXTURE_BGRX[:3], "the rest of the tile is the old colour"


def test_source_scans_tiles_sends_at_once_and_speeds_up_on_input():
    """שומר-מקור (רץ גם בלי gcc) לשלושת חלקי #1475 שאין להם טסט התנהגות כאן:
    שליחה מיד (‏deferUpdateTime 0), קצב פעיל אחרי קלט בשני ה-callbacks של
    מצב הקלט בלבד, ואין עוד סימון של כל המסך בכל טיק.
    בקרה שלילית: על הקוד שלפני #1475 אף אחת מהמחרוזות החדשות אינה בקובץ,
    והסימון המלא קיים."""
    c = _source()
    assert "screen->deferUpdateTime = 0;" in c
    assert "rfbMarkRectAsModified(screen, 0, 0, var.xres, var.yres)" not in c
    for callback in ("static void keyboard_event(", "static void pointer_event("):
        body = c[c.index(callback):]
        body = body[:body.index("\n}\n")]
        assert "input_seen();" in body, callback
    for callback in ("static void keyboard_event_ignored(",
                     "static void pointer_event_ignored("):
        body = c[c.index(callback):]
        body = body[:body.index("\n}\n")]
        assert "input_seen" not in body, f"view-only must not change anything: {callback}"
    # fb.mem: seq שלא זז = אין קריאה בכלל.
    assert "seq_before == served_seq" in c


#: ‏interfaces.md §15 — הכותרת של fb.mem: קסם, ואז width, height, stride,
#: format, seq (‏u32 LE), מרופדת ל-4096.
MEMFB_SEQ_AT = 24


def _memfb(path: Path, width: int, height: int, seq: int) -> None:
    header = b"IMCTLFB1" + struct.pack("<5I", width, height, width * 4, 1, seq)
    path.write_bytes(header.ljust(4096, b"\0") + FIXTURE_BGRX * (width * height))


@NATIVE
def test_an_unchanged_memfb_seq_is_not_even_read(tmp_path):
    """‏#1475, הדרישה מהמדידה על הברזל (60% מתהליכון בזמן שיכפול): כשהגואי לא
    הציג פריים חדש, הטיק עולה קריאה אטומית אחת. הראיה: פיקסל משתנה בקובץ
    **בלי** ש-seq זז — שום עדכון בתוך 0.8 שנ' (המוניטור לא קרא את הפיקסלים);
    ‏seq זז ב-2 (פריים שלם) — אותה בקשה נענית באריח שבו הפיקסל, ורק בו.
    בקרה שלילית: על הקוד שלפני #1475 (‏`rfbMarkRectAsModified(screen, 0, 0,
    var.xres, var.yres)` בכל טיק שה-seq זוגי) — עדכון מלא תוך טיק."""
    width, height = 128, 64
    mem = tmp_path / "fb.mem"
    _memfb(mem, width, height, seq=2)
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, extra=("--fb", str(mem)))
    try:
        with monitor.connect() as sock:
            assert _handshake(sock) == (width, height)
            first = _request_pixels(sock, width, height)
            assert first[:3] == FIXTURE_BGRX[:3], first[:4].hex()

            sock.sendall(struct.pack(">BBHHHH", 3, 1, 0, 0, width, height))
            with open(mem, "r+b") as fb:
                fb.seek(4096 + (40 * width + 100) * 4)
                fb.write(bytes([0xAA, 0xBB, 0xCC, 0x00]))
            sock.settimeout(0.8)
            try:
                early = sock.recv(1)
            except socket.timeout:
                early = b""
            assert early == b"", "the monitor read and sent a frame whose seq did not move"

            with open(mem, "r+b") as fb:
                fb.seek(MEMFB_SEQ_AT)
                fb.write(struct.pack("<I", 4))
            sock.settimeout(5)
            rects = _read_update(sock)
    finally:
        log = monitor.stop()
    assert [(x, y, w, h) for x, y, w, h, _ in rects] == [(64, 32, TILE_W, TILE_H)], (
        f"rects: {[(x, y, w, h) for x, y, w, h, _ in rects]}\nlog: {log[-1500:]}")
    pixels = rects[0][4]
    at = ((40 - 32) * TILE_W + (100 - 64)) * 4
    assert pixels[at:at + 3] == bytes([0xAA, 0xBB, 0xCC]), pixels[at:at + 4].hex()


# --- ‏#1475: מדידה — כמה עולה סריקה של 1920×1080 ----------------------------
#
# מיקרו-בנצ'מרק של לולאת הסריקה עצמה (`scan_tiles` מתוך monitor.c, מהודר
# כמו שהוא) מול מה שהקוד הישן עשה בכל טיק (‏memcpy של כל הפריים). לא טסט
# ביצועים עם סף הדוק — המספרים מודפסים לדוח (‏`-s`), והטסט נופל רק אם
# סריקה של מסך שלא השתנה יקרה פי 4 מההעתקה שהחליפה.

BENCH_C = REPO / "tests" / "monitor_scan_bench.c"


@NATIVE
def test_scan_cost_at_1920x1080_is_measured(tmp_path):
    binary = tmp_path / "monitor-scan-bench"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-I", str(REPO / "agent"), "-o", str(binary), str(BENCH_C),
         str(HMAC_C), "-lvncserver"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True,
                         timeout=120, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stdout + run.stderr
    print(run.stdout)
    got = dict(line.split("=", 1) for line in run.stdout.split() if "=" in line)
    copy_us, unchanged_us = float(got["copy_us"]), float(got["scan_unchanged_us"])
    assert float(got["scan_one_tile_us"]) > 0
    assert unchanged_us < 4 * copy_us, run.stdout
