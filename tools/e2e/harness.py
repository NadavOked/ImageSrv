"""תשתית הסימולציה: לקוח HTTP, בדיקות, המתנות, והקמת השרת.

הסימולציה מדברת עם השרת אך ורק בממשקים המתועדים (docs/interfaces.md):
ממשק 2 (hello), 3 (התשובה), 4 (התקדמות) ו-7 (זרם הקבצים). אין כאן
ייבוא של קוד השרת מלבד יצירת משתמש ההתחלה — כמו בהתקנה אמיתית.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORT = 8199
BASE = f"http://127.0.0.1:{PORT}"
#: אף אחד לא מאזין שם — זה "השרת השקט" של תרחיש ברירת המחדל.
DEAD_BASE = f"http://127.0.0.1:{PORT + 1}"
#: ‏#731 (tracer 1 של #703): הקונסולה עברה לפורט נפרד. הסימולציה מריצה
#: שרת אמיתי, ומאז הפיצול `server.main` מרים שני מאזינים — סוכן על PORT,
#: קונסולה על CONSOLE_PORT. לא PORT+1 (זה DEAD_BASE, ה"שרת השקט").
CONSOLE_PORT = PORT + 2
CONSOLE_BASE = f"http://127.0.0.1:{CONSOLE_PORT}"

#: הקידומות שחיות על אפליקציית הקונסולה (‏CONSOLE_BASE), לא על הסוכן:
#: הדף הסטטי ונתיבי הניהול `/api/console/*`.
_CONSOLE_PREFIXES = (
    "/console",
    "/api/console",
)
#: הפורט שהשרת של הסימולציה משדר עליו — ‏30199 ו-30200, ולא 9000/9001.
#: זה **לא** נוחות: השרת שהסימולציה מרימה הוא שרת אמיתי, ובמעבדה מותקן
#: `udp-sender` אמיתי ומחוברות אליה שתי מכונות פיזיות. שידור על 9000
#: הוא שידור על וילן ההפצה, ויתום ששרד ריצה שנקטעה מפיל את ההפצה הבאה
#: וזה נראה כמו תקלת רשת (#79, ‏#156, ‏#201). מספר גבוה שאינו מכיל "900"
#: — כדי ש-`ss -lunp | grep :900` יישאר בדיקה שאין בה דו-משמעות.
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
#: ‏#855: MAC שהסימולציה **אינה** כותבת לו חכירה — הראיה ששומר הזהות
#: פעיל בשרת האמיתי, ולא רק ש"כולם עוברים כי כולם על loopback".
NO_LEASE_MAC = "de:ad:be:ef:00:02"
#: כל מכונה שהסימולציה מפעילה, כולל ה-MAC "הלא מוכר" של שלב הקצוות:
#: בייצור גם מכונה לא רשומה מקבלת חכירה מ-dnsmasq (‏dhcp-range מחלק לכולם;
#: ‏tag:known של #141 מגביל רק את dhcp-boot), ו-hello שלה עונה known=false.
SIM_MACS = (BUILD_MAC, CLONER_MAC, UNKNOWN_MAC, *CLASS_MACS)

ADMIN = {"username": "noc", "password": "Sim-pass-1234!"}
DEPLOY = {"username": "madrich", "password": "Sim-deploy-99!"}

#: תוכן המחיצות של שני האימג'ים — קטן, אבל עובר את אותו מסלול בדיוק.
FILES_A = {"p1.esp.pcl.zst": b"esp-a-payload" * 300,
           "p3.windows.pcl.zst": b"windows-a-payload" * 900}
FILES_B = {"p1.esp.pcl.zst": b"esp-b-payload" * 250,
           "p3.windows.pcl.zst": b"windows-b-payload" * 1400}

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


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def disk(dev: str, size_bytes: int, serial: str, port: int | None = None) -> dict:
    """דיסק כפי שהסוכן מדווח אותו (ממשק 2).

    ‏`port` הוא החריץ הפיזי (ataN). מכונה בלי חריצים מדמה סוכן ישן או
    VM עם SCSI — השדה פשוט לא נשלח, וזה חייב להמשיך לעבוד.
    """
    entry = {"dev": dev, "size_bytes": size_bytes, "model": "SIM SSD",
             "serial": serial, "removable": False, "scheme": "gpt",
             "has_data": True}
    if port is not None:
        entry["port"] = port
    return entry


class Client:
    """לקוח HTTP קטן ששומר cookie — כמו דפדפן, בלי דפדפן.

    ‏#731: אחרי פיצול המאזינים יש שני פורטים. הלקוח מנתב לפי הנתיב —
    נתיבי קונסולה/קיוסק אל `console_base`, כל השאר (סוכן, ‎/boot, images)
    אל `base`. אותו cookie נשלח לשניהם, בדיוק כמו דפדפן שמדבר עם שני
    פורטים על אותו host. מי שמעביר `base` מפורש (למשל DEAD_BASE) מנתב
    את צד הסוכן לשם — נתיבי הקונסולה עדיין הולכים ל-console_base."""

    def __init__(self, base: str = BASE, console_base: str = CONSOLE_BASE):
        self.base = base
        self.console_base = console_base
        self.cookie = None
        # ‏#737: כותרות שהסוכן האמיתי שולח בכל בקשה (אסימון משימה).
        self.headers = {}

    def _base_for(self, path: str) -> str:
        if any(path.startswith(p) for p in _CONSOLE_PREFIXES):
            return self.console_base
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
            with urllib.request.urlopen(req, timeout=10) as response:
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
        "disks": disks,
    })


def make_manifest(source_bytes: int, files: dict[str, bytes]) -> dict:
    """מניפסט ממשק 1 לשני קבצים — ESP קטן ומחיצת windows מתרחבת."""
    esp_name, win_name = sorted(files)          # p1 לפני p3
    return {
        "schema": 1, "family": 256 if source_bytes == GB256 else 500,
        "source_disk_bytes": source_bytes, "min_target_bytes": source_bytes,
        "scheme": "gpt", "sector_size": 512,
        "disk_guid": "B8A3C1D2-4E5F-6A7B-8C9D-0E1F2A3B4C5D",   # קנוני (#1407)
        "partitions": [
            {"index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
             "start_sector": 2048, "size_bytes": 104857600,
             "used_bytes": 31457280, "file": esp_name,
             "sha256": sha(files[esp_name]), "expandable": False},
            {"index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
             "start_sector": 1085440, "size_bytes": source_bytes - 1085440 * 512,
             "used_bytes": 84509376512, "file": win_name,
             "sha256": sha(files[win_name]), "expandable": True},
        ],
        "total_compressed_bytes": sum(len(v) for v in files.values()),
        "compression": "zstd-9",
    }


def write_leases(workdir: Path) -> Path:
    """‏#855: קובץ חכירות בתבנית dnsmasq לכל מכונה מדומה — כולן על loopback.

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
    images.mkdir(parents=True)
    leases = write_leases(workdir)
    # הפלט לקובץ ולא ל-PIPE: המכונות המדומות מייצרות אלפי שורות לוג,
    # ו-PIPE שאיש לא קורא מתמלא — והשרת נחסם על הכתיבה ומפסיק לענות.
    log = (workdir / "server.log").open("wb")
    server = subprocess.Popen(
        # הכרעת נדב 11/10: השרת מסרב לכיתות כש-`capabilities.classrooms()`
        # כבוי (v1). שלבי הכיתה (6-8) בודקים את זרימת v1.2, ולכן השרת כאן
        # עולה עם היכולת דלוקה — המקבילה של monkeypatch לתהליך-בן.
        [sys.executable, "-c",
         "import server.core.capabilities as c; c.CLASSROOMS = True; "
         "from server.main import main; main()",
         "--server-url", BASE,
         "--data-dir", str(data_dir), "--images", str(images),
         "--host", "127.0.0.1", "--port", str(PORT),
         # ‏#731: פורט נפרד לקונסולה, כמו בייצור.
         "--console-port", str(CONSOLE_PORT),
         # ‏#703 (tracer 5): הקונסולה כאן על 127.0.0.1 (ברירת המחדל של
         # ‎--console-host), ולכן ``off`` מותר — הלקוח למעלה מדבר http פשוט.
         # לחיצת-היד האמיתית עם התעודה נבדקת ב-tests/test_console_tls.py.
         "--console-tls", "off",
         # ‏#201: השרת הזה אמיתי ומגיע ל-udp-sender אמיתי. בלי הדגל הזה
         # הוא משדר על פורטי ההפצה של הייצור.
         "--sender-portbase", str(SENDER_PORTBASE),
         # ‏#854: בלי udp-sender (ווינדוס, ‏CI) השולח נכשל וכשל השולח הוא
         # עכשיו כשל הסבב — הסימולציה "עברה" רק כל עוד זה נבלע בשקט.
         "--sender-simulate",
         # ‏#855: חכירות לכל מכונה מדומה — השומר פעיל, הקובץ הוא של הסימולציה.
         "--dhcp-leases", str(leases)],
        cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
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
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait_for_server(timeout: float = 25.0) -> bool:
    # ‏#731: שני מאזינים — קונסולה (‎/console/) וסוכן (GET על נתיב-POST
    # מחזיר 405, וזו ראיה שהאפליקציה עלתה). שניהם חייבים לענות לפני
    # שהסימולציה מתחילה (#1247: הקיוסק הוסר).
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (_responded(CONSOLE_BASE + "/console/")
                and _responded(BASE + "/api/v1/agent/hello")):
            return True
        time.sleep(0.4)
    return False
