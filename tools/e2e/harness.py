"""תשתית הסימולציה: לקוח HTTP, בדיקות, המתנות, והקמת השרת.

הסימולציה מדברת עם השרת אך ורק בממשקים המתועדים (docs/architecture/interfaces.md):
ממשק 2 (hello), 3 (התשובה), 4 (התקדמות) ו-7 (זרם הקבצים). אין כאן
ייבוא של קוד השרת מלבד יצירת משתמש ההתחלה — כמו בהתקנה אמיתית.
"""

from __future__ import annotations

import hashlib
import io
import json
import http.client
import os
import socket
import ssl
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
#: #3173: משתנה הסביבה שבו השרת מקבל את הקובץ של `simsend` (אותו שם כמו `simsend.ENV`).
SIMSEND_ENV = "IMAGESRV_SIM_SENDS"
PORT = 8199
BASE = f"https://127.0.0.1:{PORT}"
#: אף אחד לא מאזין שם — זה "השרת השקט" של תרחיש ברירת המחדל.
DEAD_BASE = f"http://127.0.0.1:{PORT + 1}"
#: #731 (tracer 1 של #703): הקונסולה עברה לפורט נפרד. הסימולציה מריצה
#: שרת אמיתי, ומאז הפיצול `server.main` מרים שני מאזינים — סוכן על PORT,
#: קונסולה על CONSOLE_PORT. לא PORT+1 (זה DEAD_BASE, ה"שרת השקט").
CONSOLE_PORT = PORT + 2
CONSOLE_BASE = f"http://127.0.0.1:{CONSOLE_PORT}"
#: #3486: מאזין ה-/boot (GRUB) הוא תמיד חלק מהשרת. לא ברירת המחדל של הייצור (7711, `server/core/ports.py`):
#: שרת אמיתי על אותה מכונה היה מתנגש בו.
BOOT_PORT = PORT + 3

#: הקידומות שחיות על אפליקציית הקונסולה (CONSOLE_BASE), לא על הסוכן:
#: הדף הסטטי ונתיבי הניהול `/api/console/*`.
_CONSOLE_PREFIXES = (
    "/console",
    "/api/console",
)
#: בסיס הפורטים של השרת של הסימולציה — 30199, ולא של הייצור (`chunkctx.PRODUCTION_PORTBASE`). מנוע החתיכות גוזר ממנו את
#: הפורט של הגל (`portbase` + 2, ולידו פורט הבקרה ושרתי הפריימים של המקור).
#: זה **לא** נוחות: השרת שהסימולציה מרימה הוא שרת אמיתי, ובמעבדה מחוברות אליו
#: מכונות פיזיות. שידור על פורטי ההפצה הוא שידור על וילן ההפצה, ויתום ששרד ריצה
#: שנקטעה מפיל את ההפצה הבאה וזה נראה כמו תקלת רשת (#79, #156, #201). מספר גבוה,
#: רחוק מהטווח 77xx של המוצר — כדי ש-`ss -lunp | grep :77` יישאר בדיקה שאין בה דו-משמעות.
SENDER_PORTBASE = 30199

ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
WINDOWS_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"

GB256 = 256060514304
GB500 = 500107862016

BUILD_MAC = "aa:bb:cc:00:00:10"
CLONER_MAC = "aa:bb:cc:00:00:20"
CLASS_MACS = ["b4:2e:99:07:1a:c4", "b4:2e:99:07:1a:c5",
              "b4:2e:99:07:1a:c6", "b4:2e:99:07:1a:c7"]
UNKNOWN_MAC = "de:ad:be:ef:00:01"
#: #855: MAC שהסימולציה **אינה** כותבת לו חכירה — הראיה ששומר הזהות
#: פעיל בשרת האמיתי, ולא רק ש"כולם עוברים כי כולם על loopback".
NO_LEASE_MAC = "de:ad:be:ef:00:02"
#: כל מכונה שהסימולציה מפעילה, כולל ה-MAC "הלא מוכר" של שלב הקצוות:
#: בייצור גם מכונה לא רשומה מקבלת חכירה מ-dnsmasq (dhcp-range מחלק לכולם;
#: tag:known של #141 מגביל רק את dhcp-boot), ו-hello שלה עונה known=false.
SIM_MACS = (BUILD_MAC, CLONER_MAC, UNKNOWN_MAC, *CLASS_MACS)

ADMIN = {"username": "noc", "password": "Sim-pass-1234!"}
DEPLOY = {"username": "madrich", "password": "Sim-deploy-99!"}

#: #2533: ה-XXH64 של האינדקס של כל קובץ מחיצה שנבנה כאן — `index_xxh64` במניפסט.
INDEX_XXH64: dict[bytes, str] = {}
#: #3173: מה שמחשב הבנייה מחזיק על כל קובץ מחיצה כשהוא המקור של הפצה ישירה — טקסט
#: האינדקס (מה ש-`chunkcut` כותב) וזרם הנתונים שה-BLAKE3 שלו נמדד, חתיכה-חתיכה.
CHUNKED: dict[bytes, dict] = {}
#: #3173: ה-`chunkcast` שהסימולציה בנתה (`build_chunkcast`) — לשרת (BLAKE3, `--version`) ולמקור.
CHUNKCAST: list[Path] = []


def chunk_file(payload: bytes) -> bytes:
    """קובץ מחיצה בפורמט 2 (#2533) ש-`payload` הוא זרם הנתונים שלו: זרם partclone v2
    אמיתי (כותרת עם CRC, מפת סיביות, בלוקים של 512) חתוך לפריימי zstd — כאן פריימים
    "מאוחסנים" (Raw_Blocks), ש-`zstd -dc` קורא כמו כל פריים. השרת הרץ גוזר ממנו את
    האינדקס ברקע, בדיוק כמו מקליטה אמיתית; קובץ שאינו כזה היה מסומן פגום ולא מופץ."""
    from server.agent.readbackhash import PyXxh64, list_digest
    from server.library import chunkformat

    def xxh(data: bytes) -> str:
        h = PyXxh64()
        h.update(data)
        return h.hexdigest()

    def stored(data: bytes) -> bytes:
        blocks = [data[i:i + 131072] for i in range(0, len(data), 131072)] or [b""]
        body = b"".join(((len(b) << 3) | (i == len(blocks) - 1)).to_bytes(3, "little") + b
                        for i, b in enumerate(blocks))
        return (b"\x28\xb5\x2f\xfd\xe4" + len(data).to_bytes(8, "little") + body
                + bytes.fromhex(xxh(data))[::-1][:4])

    data = payload + bytes(-len(payload) % 512)
    blocks = len(data) // 512
    head = struct.pack("<15sx14s4sH16sQQQQI", b"partclone-image", b"0.3.36", b"0002", 0xC0DE, b"SIM",
                       blocks * 512, blocks, blocks, blocks, 512)
    head += struct.pack("<IHHHHIBB", 4, 2, 64, 0, 0, 0, 1, 1)
    head += struct.pack("<I", zlib.crc32(head) ^ 0xFFFFFFFF)
    bits = bytearray(b"\xff" * ((blocks + 7) // 8))
    if blocks % 8:
        bits[-1] = (1 << (blocks % 8)) - 1
    stream = head + bytes(bits) + struct.pack("<I", zlib.crc32(bytes(bits)) ^ 0xFFFFFFFF) + data
    out = io.BytesIO()
    index, _sha, _total = chunkformat.cut(io.BytesIO(stream), out, False, compress=stored, new=PyXxh64)
    INDEX_XXH64[out.getvalue()] = list_digest(index, PyXxh64)
    CHUNKED[out.getvalue()] = {"index": index, "data": data}
    return out.getvalue()


def build_chunkcast(workdir: Path) -> Path:
    """#3173: `chunkcast` אמיתי, נבנה כמו שבונה ה-initramfs (`tests/chunkcast_rig.py`), ל-`<workdir>/bin`.

    מנוע החתיכות הוא המנוע היחיד, ושרת אינו מוציא גל בלעדיו: `chunkcast --version` הוא הראיה
    שיש שולח (`chunksend.binary_problem`), ו-`chunkcast b3list`/`b3sum` מייצרים את רשימת ה-BLAKE3
    שבלעדיה אימג' אינו מוכן לגל (`indexprep.missing`, #3332). מה שהשרת **אינו** מריץ ממנו הוא
    `send` — במקומו `simsend`. אין gcc או libzstd: הסימולציה נעצרת בשם, ואינה רצה בלי מנוע (עיקרון 5)."""
    agent = REPO / "agent"
    tool = workdir / "bin" / "chunkcast"
    tool.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["gcc", "-O2", "-pthread", "-o", str(tool), str(agent / "chunkcast.c"),
           *(str(agent / "blake3" / f) for f in
             ("blake3_one.c", "blake3_sse41_x86-64_unix.S", "blake3_sse2_x86-64_unix.S")),
           "-l:libzstd.so.1"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"FAILED: chunkcast לא נבנה ({type(exc).__name__}: {exc}) — "
                         "הסימולציה צריכה gcc ו-libzstd.so.1 (לינוקס)") from exc
    if done.returncode != 0:
        raise SystemExit(f"FAILED: chunkcast לא נבנה (gcc יצא {done.returncode}):\n{done.stderr[-2000:]}")
    CHUNKCAST[:] = [tool]
    return tool


def b3_list(data: bytes) -> bytes:
    """רשימת ה-BLAKE3 של זרם נתונים: 32 בתים לכל 4MiB (`chunkcast b3list`, כמו `chunkcut` במקור)."""
    done = subprocess.run([str(CHUNKCAST[0]), "b3list"], input=data, capture_output=True, timeout=60)
    if done.returncode != 0 or not done.stdout or len(done.stdout) % 32:
        raise SystemExit(f"FAILED: chunkcast b3list יצא {done.returncode}: {done.stderr!r}")
    return done.stdout


def b3_sum(listed: bytes) -> str:
    """ה-BLAKE3 של הרשימה עצמה — `chunk_b3` במניפסט (`chunkcast b3sum`)."""
    done = subprocess.run([str(CHUNKCAST[0]), "b3sum"], input=listed, capture_output=True, timeout=60)
    text = done.stdout.decode("ascii", "replace").strip()
    if done.returncode != 0 or len(text) != 64:
        raise SystemExit(f"FAILED: chunkcast b3sum יצא {done.returncode}: {done.stderr!r}")
    return text


def sends_path(workdir: Path) -> Path:
    """#3173: הקובץ שבו `simsend` רושם כל `chunkcast send` שהמנוע של השרת הריץ — שורת JSON לפקודה."""
    return workdir / "sends.jsonl"


def sent_commands(data_dir: Path) -> list[list[str]]:
    """הפקודות שהמנוע של השרת הריץ עד עכשיו (`simsend`), לפי הסדר. אין קובץ = לא הורץ דבר."""
    path = sends_path(data_dir.parent)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


#: תוכן המחיצות של שני האימג'ים — קטן, אבל עובר את אותו מסלול בדיוק.
FILES_A = {"p1.esp.pcl.zst": chunk_file(b"esp-a-payload" * 300),
           "p3.windows.pcl.zst": chunk_file(b"windows-a-payload" * 900)}
FILES_B = {"p1.esp.pcl.zst": chunk_file(b"esp-b-payload" * 250),
           "p3.windows.pcl.zst": chunk_file(b"windows-b-payload" * 1400)}

passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        raise SystemExit(f"FAILED: {label} {detail}")


def wait_until(fn, label: str, timeout: float = 15.0, detail=None):
    """ממתין עד ש-fn מחזירה ערך אמת — ומחזיר אותו. המכונות רצות
    בתהליכונים, ולכן רוב הבדיקות הן 'בסוף זה יקרה', לא 'זה קרה עכשיו'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            check(label, True)
            return value
        # בלי ההשהיה הלולאה מפציצה את השרת ומרוקנת פורטים בווינדוס
        # (TIME_WAIT) — וה-hello של המכונות מתעכב עד שהטיימרים משתבשים.
        time.sleep(0.1)
    extra = f" | {detail()}" if detail else ""
    check(label, False, f"לא קרה תוך {timeout} שניות{extra}")
    return None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def disk(dev: str, size_bytes: int, serial: str, port: int | None = None) -> dict:
    """דיסק כפי שהסוכן מדווח אותו (ממשק 2).

    `port` הוא החריץ הפיזי (ataN). מכונה בלי חריצים מדמה סוכן ישן או
    VM עם SCSI — השדה פשוט לא נשלח, וזה חייב להמשיך לעבוד.
    """
    entry = {"dev": dev, "size_bytes": size_bytes, "model": "SIM SSD",
             "serial": serial, "removable": False, "scheme": "gpt",
             "has_data": True}
    if port is not None:
        entry["port"] = port
    return entry


#: origin (``https://127.0.0.1:PORT``) -> the opener that verifies the agent listener there against its own CA and
#: name (#3580). A rig that starts its own server registers it with `trust_agent`.
_agent_openers: dict = {}


def _sim_opener(identity, port):
    context = ssl.create_default_context(cafile=str(identity.ca_path))

    class AgentHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            def connect(host, **kwargs):
                conn = http.client.HTTPSConnection(identity.name, port, context=context, **kwargs)
                conn._create_connection = lambda addr, *a: socket.create_connection(("127.0.0.1", port), *a)
                return conn
            return self.do_open(connect, request)

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), AgentHTTPS())


def trust_agent(base: str, identity) -> None:
    """The agent listener at `base` (https://127.0.0.1:PORT) is verified against `identity`'s CA and name."""
    parts = urllib.parse.urlsplit(base)
    _agent_openers[f"{parts.scheme}://{parts.netloc}"] = _sim_opener(identity, parts.port)


def _open(request, timeout):
    url = request if isinstance(request, str) else request.full_url
    for origin, opener in _agent_openers.items():
        if url.startswith(origin + "/") or url == origin:
            return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


class Client:
    """לקוח HTTP קטן ששומר cookie — כמו דפדפן, בלי דפדפן.

    #731: אחרי פיצול המאזינים יש שני פורטים. הלקוח מנתב לפי הנתיב —
    נתיבי קונסולה/קיוסק אל `console_base`, כל השאר (סוכן, /boot, images)
    אל `base`. אותו cookie נשלח לשניהם, בדיוק כמו דפדפן שמדבר עם שני
    פורטים על אותו host. מי שמעביר `base` מפורש (למשל DEAD_BASE) מנתב
    את צד הסוכן לשם — נתיבי הקונסולה עדיין הולכים ל-console_base."""

    def __init__(self, base: str = BASE, console_base: str = CONSOLE_BASE):
        self.base = base
        self.console_base = console_base
        self.cookie = None
        # #737: כותרות שהסוכן האמיתי שולח בכל בקשה (אסימון משימה).
        self.headers = {}

    def _base_for(self, path: str) -> str:
        if any(path.startswith(p) for p in _CONSOLE_PREFIXES):
            return self.console_base
        if self.base == BASE and path.startswith("/boot/"):
            return f"http://127.0.0.1:{BOOT_PORT}"
        return self.base

    def request(self, method, path, body=None, ctype="application/json", raw=False):
        data = body if raw else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self._base_for(path) + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", ctype)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        for key, value in self.headers.items():
            req.add_header(key, value)
        try:
            # #3173: 30 ולא 10 -- `chunk-pass` של מנוע החתיכות מריץ את שערי המוח בתוך הבקשה, ועל tr1 עמוס
            # (load 6, התור רץ) אחת מהן עברה 10 שניות והפילה את תהליכון המשכפל ב-"timed out"
            with _open(req, timeout=30) as response:
                set_cookie = response.headers.get("Set-Cookie")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def json(self, method, path, body=None):
        status, payload = self.request(method, path, body)
        try:
            return status, json.loads(payload)
        except ValueError:
            return status, payload


def hello(client: Client, mac: str, disks: list[dict]):
    """ממשק 2 — בדיוק מה שהסוכן האמיתי שולח כשה-initramfs עולה."""
    return client.json("POST", "/api/v1/agent/hello", {
        "schema": 1, "mac": mac, "all_macs": [mac], "ip": "10.44.12.50",
        "hostname_current": None, "uuid": "SIM", "firmware": "uefi",
        "secure_boot": True, "agent_version": "0.1.0", "memory_bytes": 8 << 30,
        # #3173: הסוכן מכיר את מנוע החתיכות, והוא המנוע היחיד (ממשק 2, `engines`)
        "engines": ["chunk"], "disks": disks,
    })


def make_manifest(source_bytes: int, files: dict[str, bytes]) -> dict:
    """מניפסט ממשק 1 לשני קבצים — ESP קטן ומחיצת windows מתרחבת.

    `used_bytes` פי 50 מהקובץ (2%): השרת דוחה זרם קטן מ-1% ממה שתפוס
    במחיצה (#1830, `capture.MIN_STREAM_TO_USED`)."""
    esp_name, win_name = sorted(files)          # p1 לפני p3
    return {
        "schema": 1, "image_format": 2,          # #2533: what the agent's capture sends
        "family": 256 if source_bytes == GB256 else 500,
        "source_disk_bytes": source_bytes, "min_target_bytes": source_bytes,
        "scheme": "gpt", "sector_size": 512,
        "disk_guid": "B8A3C1D2-4E5F-6A7B-8C9D-0E1F2A3B4C5D",   # קנוני (#1407)
        "partitions": [
            {"index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
             "start_sector": 2048, "size_bytes": 104857600,
             "used_bytes": 50 * len(files[esp_name]), "file": esp_name,
             "sha256": sha(files[esp_name]), "index_xxh64": INDEX_XXH64[files[esp_name]],
             "expandable": False},
            {"index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
             "start_sector": 1085440, "size_bytes": source_bytes - 1085440 * 512,
             "used_bytes": 50 * len(files[win_name]), "file": win_name,
             "sha256": sha(files[win_name]), "index_xxh64": INDEX_XXH64[files[win_name]],
             "expandable": True},
        ],
        "total_compressed_bytes": sum(len(v) for v in files.values()),
        "compression": "zstd-9",
    }


def write_leases(workdir: Path) -> Path:
    """#855: קובץ חכירות בתבנית dnsmasq לכל מכונה מדומה — כולן על loopback.

    השרת שהסימולציה מרימה הוא אמיתי, ושומר הזהות שלו **אינו מכובה**: הוא
    מקבל את הקובץ הזה ב---dhcp-leases במקום את קובץ המכונה, בדיוק כמו
    שהמעבדה מקבלת את הקובץ של dnsmasq על br0."""
    path = workdir / "dnsmasq.leases"
    lines = [f"1789000000 {mac} 127.0.0.1 sim-{i} 01:{mac}"
             for i, mac in enumerate(SIM_MACS)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def start_server(workdir: Path) -> tuple[subprocess.Popen, Path, Path]:
    data_dir, images = workdir / "data", workdir / "images"
    from server.agent.tls import ensure_identity
    trust_agent(BASE, ensure_identity(data_dir)[0])
    images.mkdir(parents=True)
    leases = write_leases(workdir)
    # הפלט לקובץ ולא ל-PIPE: המכונות המדומות מייצרות אלפי שורות לוג,
    # ו-PIPE שאיש לא קורא מתמלא — והשרת נחסם על הכתיבה ומפסיק לענות.
    log = (workdir / "server.log").open("wb")
    # #3173: ה-`chunkcast` שנבנה (`build_chunkcast`) ראשון ב-PATH של השרת, והקובץ שבו
    # השולח המדומה רושם את מה שהמנוע הריץ.
    env = dict(os.environ, PATH=os.pathsep.join([str(workdir / "bin"), os.environ.get("PATH", "")]))
    env[SIMSEND_ENV] = str(sends_path(workdir))
    server = subprocess.Popen(
        # הכרעת נדב 11/10: השרת מסרב לכיתות כש-`capabilities.classrooms()`
        # כבוי (v1). השרת כאן עולה עם היכולת **דלוקה** — המקבילה של monkeypatch
        # לתהליך-בן — כדי ששלב 6 יוכיח שסבב כיתה נדחה גם כשהכיתות דלוקות: לתחנה
        # אין נתיב במנוע החתיכות (#3173), והסירוב הוא של הסבב, לא רק של המהדורה.
        # #3173: ו-`simsend` במקום `chunkcast send` — המנוע אמיתי, הפקודה אינה משדרת.
        [sys.executable, "-c",
         ("import server.core.capabilities as c; c.CLASSROOMS = True; "
          "import tools.e2e.simsend as s; s.install(); "
          "from server.main import main; main()"),
         "--server-url", BASE,
         "--data-dir", str(data_dir), "--images", str(images),
         "--host", "127.0.0.1", "--port", str(PORT),
         # #731: פורט נפרד לקונסולה, כמו בייצור.
         "--console-port", str(CONSOLE_PORT), "--boot-port", str(BOOT_PORT),
         # #703 (tracer 5): הקונסולה כאן על 127.0.0.1 (ברירת המחדל של
         # --console-host), ולכן ``off`` מותר — הלקוח למעלה מדבר http פשוט.
         # לחיצת-היד האמיתית עם התעודה נבדקת ב-tests/test_console_tls.py.
         "--console-tls", "off",
         # #201: השרת הזה אמיתי. בלי הדגל הזה פורט הגל שלו הוא של הייצור.
         "--sender-portbase", str(SENDER_PORTBASE),
         # #3173: אין כאן `--sender-simulate` ואין `stream_engine`: השרת רץ על ברירת
         # המחדל — מנוע החתיכות — והשולח המדומה הוא `simsend` (למעלה).
         # #855: חכירות לכל מכונה מדומה — השומר פעיל, הקובץ הוא של הסימולציה.
         "--dhcp-leases", str(leases)],
        cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT, env=env,
    )
    return server, data_dir, images


#: תחילת רשומת לוג של uvicorn. כל מה שביניהן שייך לרשומה הקודמת —
#: וזה בדיוק מה שמאפשר לגזור traceback שלם, כולל חריגות משורשרות.
_LOG_RECORD = re.compile(r"(INFO|ERROR|WARNING|CRITICAL|DEBUG)[:\s]")

TRACEBACK_HEAD = "Traceback (most recent call last):"


def server_tracebacks(text: str, limit: int = 3) -> list[str]:
    """ה-traceback-ים ביומן השרת; מוחזרים האחרונים, בסדר הופעתם."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith(TRACEBACK_HEAD):
            current = [line]
            blocks.append(current)
        elif current is not None:
            if _LOG_RECORD.match(line):
                current = None          # הרשומה הבאה — סוף ה-traceback
            else:
                current.append(line)
    return ["\n".join(block) for block in blocks[-limit:]]


def server_log_tail(workdir: Path, lines: int = 40) -> str:
    """סוף יומן השרת — ולפניו החריגות שנמצאו בכל אורכו.

    ארבע מכונות דוגמות hello שלוש פעמים בשנייה והמבט-על נדגם עשר פעמים
    בשנייה: היומן מוצף שורות גישה, ולכן 40 השורות האחרונות הן כמעט תמיד
    *אחרי* ה-traceback שגרם לכישלון. כך נראה כשל ב-CI שכל מה שנשאר ממנו
    הוא "החזיר 500" בלי הסבר, ושדרש הרצה חוזרת כדי להיחקר. החריגה היא
    הדבר היחיד שבאמת מסביר, ולכן היא נשלפת בנפרד ומודפסת ראשונה.
    """
    try:
        text = (workdir / "server.log").read_text(errors="replace")
    except OSError:
        return "(אין server.log)"
    parts = []
    tracebacks = server_tracebacks(text)
    if tracebacks:
        parts.append(f"--- חריגות בשרת: {text.count(TRACEBACK_HEAD)} סה\"כ,"
                     f" {len(tracebacks)} האחרונות ---")
        parts.extend(tracebacks)
    parts.append(f"--- {lines} השורות האחרונות של יומן השרת ---")
    parts.append("\n".join(text.splitlines()[-lines:]))
    return "\n".join(parts)


def _responded(url: str) -> bool:
    """השרת מאזין אם הגיעה **תשובת HTTP** כלשהי — גם 404/405. רק סירוב
    חיבור/URLError אומר שהוא עוד לא עלה (#731: בודקים כל מאזין בנפרד)."""
    try:
        with _open(url, timeout=2):
            pass
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait_for_server(timeout: float = 25.0) -> bool:
    # #731: שני מאזינים — קונסולה (/console/) וסוכן (GET על נתיב-POST
    # מחזיר 405, וזו ראיה שהאפליקציה עלתה). שניהם חייבים לענות לפני
    # שהסימולציה מתחילה (#1247: הקיוסק הוסר).
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (_responded(CONSOLE_BASE + "/console/")
                and _responded(BASE + "/api/v1/agent/hello")):
            return True
        time.sleep(0.4)
    return False
