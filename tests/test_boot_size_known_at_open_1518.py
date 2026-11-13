"""כל תשובה של /boot אומרת את גודלה בכותרות — לא chunked (#1518).

GRUB 2.12 של EFI (ה-grubx64.efi החתום של דביאן, `tpm` מובנה בו) רושם את
מאמת ה-TPM בכל מכונה שהקושחה שלה מציגה TPM. המאמת תובע **כל** קובץ, ו-
`grub_verifiers_open` (`grub-core/kern/verifiers.c`) קורא אותו כולו מיד
אחרי ה-open — וקובץ שגודלו עוד לא ידוע נדחה שם: "big file signature isn't
implemented yet". `http_open` חוזר ברגע שהכותרות הגיעו; בתשובת chunked
הגודל נקבע רק כשצ'אנק הסיום נקרא, וזה תלוי תזמון. התפריט של מחשב הבנייה
(5601 בייט על החוט) גדול מחלון הפתיחה של השרת (10 × MSS 536 = 5360),
ולכן צ'אנק הסיום יוצא רק אחרי ה-ACK של GRUB — תמיד מאוחר מדי.

על ברזל (18/10, rc5): מחשב הבנייה משך `/boot/menu` שלוש פעמים בשנייה
אחת ועלה מהדיסק. במעבדה (QEMU+OVMF+swtpm, אותו grubx64.efi מ-TA, RTT
20ms): עם TPM ו-chunked — שלוש משיכות ונפילה לדיסק; בלי TPM, או עם
Content-Length — אתחול מלא. i386-pc (מחשבי השיכפול) אין בו מודול tpm,
ולכן chunked "עבד" שם — 11 אתחולים של משכפל 2 על rc5.

הטסט כאן הוא מודל של מה ש-GRUB יודע ברגע ה-open: **רק הכותרות**. הוא
מפרסר אותן כמו `parse_line` של `grub-core/net/http.c` (השוואת תחילית
תלוית-רישיות), ודורש שהגודל ידוע מהן לבדן — בלי להסתמך על כך שהגוף
"כבר הגיע", כי זה בדיוק מה שהיה נכון במעבדה ושגוי על ברזל.
"""

import socket
import threading

import pytest

from boot.grub_menu import GrubConfig
from boot.http import create_boot_asgi

CONFIG = GrubConfig(server_base="http://127.0.0.1:8080")
MAC = "c8:d9:d2:0b:fe:32"

#: התשובה שהשרת נותן לכל תפקיד; `build` הוא זה שנפל על ברזל.
ANSWERS = {
    "build": {"schema": 1, "known": True, "role": "build"},
    "cloner": {"schema": 1, "known": True, "role": "cloner"},
    "classroom-task": {"schema": 1, "known": True, "role": "classroom",
                       "task": {"type": "deploy"}},
    "unregistered": {"schema": 1, "known": False},
}

#: `PACKAGE_STRING` של GRUB 2.12, כפי שהוא נשלח ב-`http_establish`.
GRUB_REQUEST = "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nUser-Agent: GRUB 2.12\r\n\r\n"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _agent_like_app(boot_dir, answer):
    """כמו מאזין הסוכן: /boot מוגש באפליקציית האתחול, ממופה על /boot."""
    (boot_dir / "vmlinuz").write_bytes(b"k" * 4096)
    boot = create_boot_asgi(resolve=lambda mac, ip: dict(answer), config=CONFIG,
                            boot_dir=boot_dir, record=lambda mac, step: True)

    async def app(scope, receive, send):
        await boot(dict(scope, root_path="/boot"), receive, send)
    return app


@pytest.fixture(params=sorted(ANSWERS))
def live_agent(request, tmp_path):
    """uvicorn אמיתי בתצורה של מאזין הסוכן (`agent_uvicorn_config`, h11)."""
    uvicorn = pytest.importorskip("uvicorn")
    from server import main as server_main
    port = _free_port()
    config = server_main.agent_uvicorn_config(
        _agent_like_app(tmp_path, ANSWERS[request.param]), "127.0.0.1", port)
    config.lifespan = "off"
    config.log_level = "warning"
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


def grub_open(sock: socket.socket) -> tuple[int | None, bool, bytes]:
    """מה ש-GRUB יודע כש-`http_open` חוזר: הכותרות, ולא בייט אחד מעבר.

    מחזיר (גודל או None, chunked?, מה שכבר נקרא מעבר לכותרות). הפרסור הוא
    של `parse_line`: `grub_memcmp` על תחילית, תלוי-רישיות — "content-length"
    באותיות קטנות אינו גודל עבור GRUB (#12).
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        assert chunk, "השרת סגר לפני סוף הכותרות"
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    assert lines[0].startswith(b"HTTP/1.1 200"), lines[0]
    size, chunked = None, False
    for line in lines[1:]:
        if line.startswith(b"Content-Length: ") and size is None:
            size = int(line[len(b"Content-Length: "):])
        if line.startswith(b"Transfer-Encoding: chunked"):
            chunked = True
    return size, chunked, rest


def _fetch(port: int, path: str) -> tuple[int | None, bool, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(GRUB_REQUEST.format(path=path).encode())
        size, chunked, body = grub_open(sock)
        if size is not None:
            while len(body) < size:
                chunk = sock.recv(65536)
                assert chunk, "השרת סגר לפני סוף הגוף"
                body += chunk
        return size, chunked, body


@pytest.mark.parametrize("path", [f"/boot/menu?mac={MAC}",
                                  f"/boot/step?mac={MAC}&s=entry",
                                  f"/boot/step?mac={MAC}&s=pre-boot",
                                  "/boot/vmlinuz"])
def test_grub_knows_the_size_when_open_returns(live_agent, path):
    """בלי גודל בכותרות, `grub_verifiers_open` דוחה את הקובץ על מכונת
    EFI עם TPM, ו-configfile נכשל לפני שקרא שורה (מחשב הבנייה, 18/10)."""
    size, chunked, body = _fetch(live_agent, path)
    assert not chunked, (f"{path}: Transfer-Encoding: chunked — ב-EFI עם TPM, \u200fGRUB "
                         "דוחה קובץ שגודלו לא ידוע ב-open (big file signature isn't "
                         "implemented yet)")
    assert size is not None, f"{path}: אין Content-Length בכותרות — הגודל לא ידוע ב-open"
    assert len(body) == size


def test_the_menu_body_is_the_whole_menu(live_agent):
    """הגודל שבכותרת הוא של התפריט כולו — לא של חלק ממנו."""
    size, _, body = _fetch(live_agent, f"/boot/menu?mac={MAC}")
    assert size == len(body) > 0
    assert body.startswith(b"# ")
    assert b"\r\n0\r\n" not in body  # אין מסגור של צ'אנקים בתוך הגוף
