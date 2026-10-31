"""נתיבי ‎/boot לא סוגרים את החיבור ראשונים (#1423).

‏GRUB יוצא מאותם פורטי מקור בכל אתחול (21550, 21551, ...), עם ISN קטן
ובלי timestamps, ואינו עונה ב-RST. כשהשרת סגר ראשון (`Connection: close`)
הוא נשאר ב-TIME_WAIT לשישים שניות על אותו 4-tuple, ואתחול חוזר מהיר נתקע
על כל פורט כזה: 40 SYN ב-15.6 שניות, ורק אז GRUB עובר לפורט הבא.

הבדיקה כאן כפולה, כי לכל שכבה שקר משלה: האפליקציה יכולה לא לשלוח את
הכותרת, ו-uvicorn עדיין יכול לסגור בעצמו. לכן גם uvicorn אמיתי על socket.

והחיבור האחרון: GRUB סוגר אותו עם חלון אפס ועובר לאתחל, בלי לאשר את ה-FIN
של השרת. בלי RST השרת נשאר ב-LAST-ACK ותוקע את האתחול הבא באותה דרך
(2 מתוך 5 אתחולים חוזרים במעבדה). ‏`server/net/grub_close.py` עונה ב-RST.
"""

import asyncio
import socket
import threading

import pytest

from boot.grub_menu import GrubConfig
from boot.http import create_boot_asgi
from native import requires_native

CONFIG = GrubConfig(server_base="http://127.0.0.1:8080")
ANSWER = {"schema": 1, "known": True, "role": "classroom", "task": {"type": "deploy"}}
MAC = "b4:2e:99:07:1a:c4"

#: כל סוגי התשובות של ‎/boot: תפריט, פירור, קובץ אתחול, ו-404.
PATHS = ["/menu", "/step", "/vmlinuz", "/no-such-file"]


def _app(boot_dir):
    (boot_dir / "vmlinuz").write_bytes(b"k" * 4096)
    return create_boot_asgi(resolve=lambda mac, ip: dict(ANSWER), config=CONFIG,
                            boot_dir=boot_dir, record=lambda mac, step: True)


def _start_headers(app, path: str) -> list:
    query = f"mac={MAC}&s=entry" if path == "/step" else f"mac={MAC}"
    scope = {"type": "http", "method": "GET", "path": path, "root_path": "",
             "query_string": query.encode(), "headers": [],
             "client": ("10.44.0.196", 21550), "server": ("127.0.0.1", 8080)}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return next(m["headers"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.parametrize("path", PATHS)
def test_no_boot_answer_asks_to_close_the_connection(tmp_path, path):
    headers = _start_headers(_app(tmp_path), path)
    names = [name for name, _ in headers]
    # ראיה חיובית שזו תשובה של ‎/boot: BASE_HEADERS באותיות גדולות (#12).
    assert b"Cache-Control" in names
    assert [v for n, v in headers if n.lower() == b"connection"] == []


# ---------- uvicorn אמיתי ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_boot(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(_app(tmp_path), host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        thread.join(0.05)
    assert server.started, "uvicorn לא עלה"
    yield port
    server.should_exit = True
    thread.join(5)


def _read_response(sock: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        assert chunk, "השרת סגר לפני סוף הכותרות"
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                  if line.lower().startswith(b"content-length:"))
    while len(body) < length:
        chunk = sock.recv(65536)
        assert chunk, "השרת סגר לפני סוף הגוף"
        body += chunk
    return head, body


@pytest.mark.parametrize("path", PATHS)
def test_the_server_does_not_close_first_on_the_wire(live_boot, path):
    """בקשה כמו של GRUB (HTTP/1.1, בלי כותרת Connection). אחרי הגוף המלא
    החיבור עדיין פתוח — מי שסוגר ראשון הוא הלקוח, ו-TIME_WAIT נשאר אצלו."""
    query = f"?mac={MAC}&s=entry" if path == "/step" else f"?mac={MAC}"
    with socket.create_connection(("127.0.0.1", live_boot), timeout=5) as sock:
        sock.sendall(f"GET {path}{query} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                     "User-Agent: GRUB 2.12\r\n\r\n".encode())
        head, _ = _read_response(sock)
        assert b"connection: close" not in head.lower()
        sock.settimeout(1.0)
        # ‏recv שמחזיר b"" הוא FIN מהשרת — בדיוק הבאג. פקיעת זמן היא הראיה
        # החיובית שהחיבור עוד פתוח.
        with pytest.raises(socket.timeout):
            extra = sock.recv(1)
            pytest.fail(f"השרת סגר ראשון (recv החזיר {extra!r})")


# ---------- החיבור האחרון של GRUB: RST ולא LAST-ACK ----------

def _agent_like_app(boot_dir):
    """כמו מאזין הסוכן: ‎/boot מוגש באפליקציית האתחול, כל השאר API רגיל."""
    boot = _app(boot_dir)

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/boot/"):
            await boot(dict(scope, root_path="/boot"), receive, send)
            return
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


@pytest.fixture()
def live_agent(tmp_path):
    """מאזין כמו של השרת: התצורה נבנית ב-`agent_uvicorn_config` של main."""
    uvicorn = pytest.importorskip("uvicorn")
    from server import main as server_main
    port = _free_port()
    # בלי הפונקציה (הקוד שלפני #1423) — uvicorn רגיל, והטסט נכשל על FIN.
    make = getattr(server_main, "agent_uvicorn_config", None)
    app = _agent_like_app(tmp_path)
    config = (make(app, "127.0.0.1", port) if make
              else uvicorn.Config(app, host="127.0.0.1", port=port))
    config.lifespan = "off"
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        thread.join(0.05)
    assert server.started, "uvicorn לא עלה"
    yield port
    server.should_exit = True
    thread.join(5)


def _close_after(port: int, path: str) -> bytes | type:
    """בקשה, קריאת התשובה, סגירת צד הכתיבה כמו GRUB — ומה השרת עשה:
    ‏b"" הוא FIN, ‏ConnectionResetError הוא RST."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        _read_response(sock)
        sock.shutdown(socket.SHUT_WR)
        try:
            return sock.recv(1)
        except ConnectionResetError:
            return ConnectionResetError


@requires_native(posix=True, why="בלולאת ה-proactor של ווינדוס eof_received לא נקרא; "
                                 "השרת רץ על לינוקס")
@pytest.mark.parametrize("path", [f"/boot/step?mac={MAC}&s=pre-boot",
                                  f"/boot/menu?mac={MAC}", "/boot/vmlinuz"])
def test_grub_closing_a_boot_connection_gets_a_reset(live_agent, path):
    """בלי RST השרת נשאר ב-LAST-ACK: ‏GRUB סוגר עם חלון אפס ולא מאשר את
    ה-FIN של השרת, והאתחול הבא נתקע על אותו פורט מקור."""
    assert _close_after(live_agent, path) is ConnectionResetError


def test_other_connections_still_close_with_fin(live_agent):
    assert _close_after(live_agent, "/api/v1/agent/state") == b""
