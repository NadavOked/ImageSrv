"""תשתית משותפת לבדיקות השרת: ספרייה מזויפת, שעון נשלט, ולקוחות
מחוברים בשני התפקידים — ושתי בדיקות על הריצה עצמה (#52, ‏#79).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import hostguard
import hygiene
import native
import orphan_guard as _orphan_guard
from orphan_guard import orphan_guard  # noqa: F401 -- autouse fixture (#1736)

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


# --- כנות הריצה: כלי שחסר, ותהליך ששרד (#52, ‏#79) --------------------------
#
# ‏"N עברו, אפס דילוגים" הוא המשפט שכל האימות של הפרויקט נשען עליו,
# ולכן המכשיר עצמו צריך בדיקה: חבילה שדילגה במקום שהכלים אמורים להיות
# אינה ירוקה, וטסט שהשאיר תהליך שידור חי לא סיים.

_skips = native.SkipAudit()
_run: dict[str, str | None] = {"basetemp": None}

#: #312 — כשלי ווינדוס שמותר להם להישאר אדומים. ריק בכוונה: כל מקרה
#: סביבתי הוא skipif מוצהר או תיקון. הוספה לכאן היא בדיוק הגידול
#: שה-Issue אוסר — הטסט ב-test_hygiene נופל אם הקבוצה חדלה להיות ריקה.
WINDOWS_ALLOWED_FAILURES: frozenset[str] = frozenset()
_win_failed: list[str] = []


def pytest_report_header(config) -> str:
    return _orphan_guard.header_line()


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", f"{native.MISSING_MARK}(tools): כלי מקומי חסר במקום שנדרש"
    )
    # ‏#949: חמשת טסטי evdev כותבים ל-/dev/uinput וקוראים /dev/input/event* —
    # במעבדה root; ב-CI הציבורי הם רצים בצעד נפרד תחת sudo (tests.yml).
    config.addinivalue_line(
        "markers", "uinput: דורש /dev/uinput כתיב וקריאת /dev/input — root או sudo"
    )
    # ‏#1737: הרזרבה למערכת (‏10GiB) — רק בטסטים שבודקים אותה; ראו `_host_free_space`.
    config.addinivalue_line(
        "markers", "system_reserve: הטסט בודק את הרזרבה של disk_reserve — היא נשארת 10GiB"
    )
    # ‏#1334: אינטגרציית ה-VM (‏qemu, ‏ovmf, ‏guestfs, ‏root) — opt-in במעבדה בלבד.
    # ב-CI הציבורי היא **מוסרת במפורש** (-m "not vm_it"), לא "מדולגת": דילוג
    # תחת REQUIRE_NATIVE=1 הוא כישלון, וזה מה שהפיל את tests מאז v0.73.2.
    config.addinivalue_line(
        "markers", "vm_it: אינטגרציית עדכון ה-VM — IMAGESRV_VM_IT=1, root וכלי השרת"
    )
    # ‏#2118: רגיש לעומס (זרם שמחכה ‏WAIT_STREAM_STALL, ערך של המוצר) — נכשל תחת
    # ‏`-n auto` ועובר לבדו. רץ סדרתית אחרי השלב המקבילי: ‏`-m "not serial"`
    # ואז ‏`-m serial`; השער סופר את שני השלבים יחד (lab_gate.py).
    config.addinivalue_line(
        "markers", "serial: רגיש לעומס; רץ סדרתית אחרי השלב המקבילי, #2118"
    )
    # לפני האיסוף: ‏`requires_native(("sgdisk", which("sgdisk")))` נקבע ביבוא
    # המודול, ו-busybox ash של הטסט מוצא את sgdisk באותו PATH (#1594).
    if os.name != "nt":
        os.environ["PATH"] = native.with_sbin(os.environ.get("PATH", ""))
    hygiene.block_real_processes()
    # ‏portbase גבוה ואקראי לכל הריצה: יתום של ריצה שנקטעה לא ינקה איש,
    # ולכן ההגנה היא שלא יהיה לו במה להתנגש (#156).
    hygiene.assign_test_portbase()
    # ‏#2099: תחת xdist — לוג שולח לכל worker; סדרתי — כמו קודם.
    hygiene.isolate_sender_log(os.environ.get("PYTEST_XDIST_WORKER"))
    hostguard.block_real_host_reads()
    hostguard.detach_host_dev()


#: קובץ עקבות אופציונלי — ‏`IMAGESRV_TEST_TRACE=<נתיב>`.
#:
#: ‏07/09: ריצה מלאה נגמרה ב-87% **בלי שורת סיכום כלל**, כלומר לא היה
#: ממה לקרוא תוצאה. מוות כזה הוא תהליך-בן שהרג את הריצה לפני
#: ‏`pytest_sessionfinish`, ו-`faulthandler_timeout` — מסלול האבחון
#: הרגיל — **אינו זמין בווינדוס**. לכן הקובץ הזה: הוא כתוב ומסונכרן
#: לדיסק **לפני** כל טסט, ולכן מוות שקט משאיר את שם הטסט האחרון.
#:
#: כבוי כברירת מחדל: כתיבה מסונכרנת לכל טסט היא תקורה אמיתית, והיא
#: נחוצה רק כשמחפשים את הכשל הזה.
_trace = os.environ.get("IMAGESRV_TEST_TRACE")


def pytest_runtest_setup(item) -> None:
    if _trace:
        # ‏`flush` **ו-**`fsync`: חוצץ שנשאר בזיכרון נעלם עם התהליך,
        # וזה בדיוק המקרה שהקובץ הזה אמור לתעד.
        with open(_trace, "a", encoding="utf-8") as fh:
            fh.write(f"{item.nodeid}\n")
            fh.flush()
            os.fsync(fh.fileno())
    native.fail_on_missing_native(item)


def pytest_runtest_logreport(report) -> None:
    _skips.record(report)
    if os.name == "nt" and report.failed and report.nodeid not in _win_failed:
        _win_failed.append(report.nodeid)


def _say(lines: list[str]) -> None:
    text = "\n" + "\n".join(lines)
    try:
        print(text)
    except UnicodeEncodeError:           # pragma: no cover — קונסולת cp1252
        print(text.encode("ascii", "backslashreplace").decode())


#: ‏#2099: תחת pytest-xdist המנהל (controller) קובע את קוד היציאה, והוא
#: **מתעלם** מ-`session.exitstatus` של worker (נקרא רק `exitstatus == 2`,
#: ‏Ctrl-C). תהליך יתום או שולח אמיתי שה-worker מצא היו נעלמים בשקט — ריצה
#: אדומה שנראית ירוקה (עיקרון 5). לכן ה-worker מוסר את הממצאים דרך
#: ‏`workeroutput`, והמנהל מוסיף אותם לפסק שלו. הדילוגים וכשלי ווינדוס אינם
#: עוברים כאן: הדוחות עצמם מגיעים למנהל (`pytest_runtest_logreport`).
WORKER_PROBLEMS_KEY = "imagesrv_problems"
_worker_problems: list[str] = []


def _hygiene_problems() -> list[str]:
    problems = hygiene.session_verdict(_run["basetemp"], native.native_required())
    if hygiene.blocked_spawns:
        problems.append(f"{len(hygiene.blocked_spawns)} ניסיונות להפעיל שולח אמיתי:")
        problems += [f"    {' '.join(cmd)}" for cmd in hygiene.blocked_spawns]
    return problems


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    """הוק של xdist במנהל: worker סיים — ממצאי ההיגיינה שלו נאספים (#2099)."""
    _worker_problems.extend(getattr(node, "workeroutput", {}).get(WORKER_PROBLEMS_KEY, []))


#: ‏#2121: שער המעבדה אינו קורא את קוד היציאה (הוא 1 בכל ריצה על ה-testrunner,
#: שש שגיאות uinput), ולכן יתום או שולח אמיתי שנמצאו כאן — שאינם טסט ב-junit —
#: היו נעלמים בו. ‏`IMAGESRV_SESSION_PROBLEMS=<נתיב>`: הרשימה נכתבת תמיד, גם
#: ריקה — קובץ חסר אומר "לא נרשם", לא "אין בעיות" (עיקרון 5).
SESSION_PROBLEMS_ENV = "IMAGESRV_SESSION_PROBLEMS"


def _record_session_problems(problems: list[str]) -> None:
    path = os.environ.get(SESSION_PROBLEMS_ENV)
    if path:
        Path(path).write_text(json.dumps(problems, ensure_ascii=False), encoding="utf-8")


def pytest_sessionfinish(session, exitstatus) -> None:
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:          # worker של xdist — המנהל פוסק (#2099)
        workeroutput[WORKER_PROBLEMS_KEY] = _hygiene_problems()
        return
    # דילוג מוצהר אינו כישלון — אבל גם אינו שקט: מספר וסיבה, בכל ריצה (#295).
    notes = _skips.notes()
    if notes:
        _say(notes)
    problems = _skips.verdict()
    session_problems = _hygiene_problems() + _worker_problems
    _record_session_problems(session_problems)
    problems += session_problems
    if os.name == "nt":
        extra = [n for n in _win_failed if n not in WINDOWS_ALLOWED_FAILURES]
        if extra:
            problems.append(
                f"#312: {len(extra)} כשלים בווינדוס שאינם ברשימה המוכרת "
                "(גידול; skipif עם נימוק או תיקון, לא allowlist):"
            )
            problems += [f"    {n}" for n in extra]
    if not problems:
        return
    _say(problems)
    session.exitstatus = 1


@pytest.fixture(scope="session", autouse=True)
def _run_basetemp(tmp_path_factory) -> None:
    """תיקיית ה-tmp של הריצה הזאת. רק תהליכים שמזכירים אותה ייסרקו
    וייהרגו — ריצה מקבילה של מישהו אחר על אותו שרת אינה שלנו."""
    _run["basetemp"] = str(tmp_path_factory.getbasetemp())


@pytest.fixture(autouse=True)
def _no_real_sender():
    """טסט שהגיע לשולח האמיתי נכשל בשמו, ולא בשקט אחרי הריצה."""
    before = len(hygiene.blocked_spawns)
    yield
    escaped = hygiene.blocked_spawns[before:]
    if escaped:
        pytest.fail(
            "הטסט הגיע ל-udp-sender האמיתי במקום לשולח מזויף (#79): "
            + " | ".join(" ".join(cmd) for cmd in escaped),
            pytrace=False,
        )

WINDOWS_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"

MANIFEST_256 = {
    "schema": 1,
    "id": "img_7f3a91",
    "name": "Office 2024 Standard",
    "description": "האימג' הנפוץ ביותר",
    "folder": "Office",
    "created": "2026-08-22T09:14:00+03:00",
    "created_by": "nadav",
    "family": 256,
    "source_disk_bytes": 256060514304,
    "min_target_bytes": 256060514304,
    "scheme": "gpt",
    "sector_size": 512,
    "partitions": [
        {
            "index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
            "start_sector": 2048, "size_bytes": 104857600, "used_bytes": 31457280,
            "file": "p1.esp.pcl.zst", "sha256": "aa" * 32, "expandable": False,
        },
        {
            "index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
            "start_sector": 1085440, "size_bytes": 254803968000,
            "used_bytes": 84509376512,
            "file": "p3.win.pcl.zst", "sha256": "bb" * 32, "expandable": True,
        },
    ],
    "total_compressed_bytes": 57982058496,
    "partclone_version": "0.3.x",
    "compression": "zstd-9",
    "field_from_the_future": "ignored",   # שדה לא ידוע — מתעלמים, לא נכשלים
}

MANIFEST_500 = {
    **MANIFEST_256,
    "id": "img_2c8e04",
    "name": "CAD Heavy",
    "family": 500,
    "min_target_bytes": 500107862016,
}


class Clock:
    """שעון נשלט — הבדיקות מזיזות אותו במקום לישון."""

    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


LINUX_GUID = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
RECOVERY_GUID = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"

#: אימג' Linux (אפיון סעיף 14): ESP, שורש ext4, ו-swap שמתועד אבל לא נשמר.
MANIFEST_LINUX = {
    **MANIFEST_256,
    "id": "img_4c0de1",
    "name": "Ubuntu 24.04 Linux Course",
    "folder": "Linux",
    "os": "linux",
    "partitions": [
        MANIFEST_256["partitions"][0],
        {
            "index": 2, "type_guid": SWAP_GUID, "role": "swap", "fs": "swap",
            "start_sector": 206848, "size_bytes": 8589934592, "used_bytes": 0,
            "file": None, "sha256": None, "expandable": False,
        },
        {
            "index": 3, "type_guid": LINUX_GUID, "role": "linux", "fs": "ext4",
            "start_sector": 16984064, "size_bytes": 247364194304,
            "used_bytes": 21474836480,
            "file": "p3.linux.pcl.zst", "sha256": "cc" * 32, "expandable": True,
        },
    ],
}
PARTITION_BYTES = b"compressed-partition-bytes"


def write_image(root: Path, manifest: dict) -> None:
    """כותב אימג' מזויף לדיסק — עם sha256 אמיתי.

    הערכים בקבועים למעלה הם ממלאי מקום לצורת המבנה; מה שנכתב לדיסק
    חייב להיות עקבי עם עצמו, אחרת בדיקות שמאמתות אימג' (ייבוא, שחזור)
    נכשלות על נתוני בדיקה ולא על באג.
    """
    folder = root / manifest["id"]
    folder.mkdir(parents=True)
    digest = hashlib.sha256(PARTITION_BYTES).hexdigest()
    on_disk = copy.deepcopy(manifest)
    for part in on_disk["partitions"]:
        if part.get("file"):
            part["sha256"] = digest
    (folder / "manifest.json").write_text(
        json.dumps(on_disk, ensure_ascii=False), encoding="utf-8"
    )
    for part in on_disk["partitions"]:
        if part.get("file"):
            (folder / part["file"]).write_bytes(PARTITION_BYTES)


@pytest.fixture()
def images_root(tmp_path: Path) -> Path:
    root = tmp_path / "images"
    write_image(root, MANIFEST_256)
    write_image(root, MANIFEST_500)
    return root


@pytest.fixture()
def clock() -> Clock:
    return Clock()


def complete_console_login(client, conn, username: str, password: str) -> dict:
    """כניסה מלאה: מסיימת החלפת סיסמה / הרשמת MFA / TOTP לפי מה שהשרת דורש."""
    from server.console import totp as totp_mod

    r = client.post(
        "/api/console/login", json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    if body.get("must_change_password"):
        raise AssertionError(f"{username} דורש החלפת סיסמה — לא זרע בדיקות")
    if body.get("mfa_enrollment_required"):
        setup = client.post("/api/console/me/mfa/setup")
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]
        en = client.post(
            "/api/console/me/mfa/enable", json={"code": totp_mod.totp_code(secret)},
        )
        assert en.status_code == 200, en.text
        return en.json()
    if body.get("mfa_required"):
        secret = conn.execute(
            "SELECT mfa_secret FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()["mfa_secret"]
        r2 = client.post(
            "/api/console/login/mfa",
            json={"challenge": body["challenge"], "code": totp_mod.totp_code(secret)},
        )
        assert r2.status_code == 200, r2.text
        return r2.json()
    return body


def _build_server(tmp_path: Path, images_root: Path, clock: Clock, recorder) -> dict:
    """אפליקציה מלאה עם שולח מזויף, ולקוחות מחוברים בשני התפקידים."""
    from server.console import users
    from server.app import create_app

    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=recorder,
        health_hooks={"interfaces": lambda: []},
    )
    ctx = app.state.ctx
    # סיסמאות הפיקסטורה קדמו לנוהל #1085; check_policy=False רק לזרע הבדיקות.
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test",
                 check_policy=False)

    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    assert deploy.post(
        "/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}
    ).status_code == 200
    return {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
            "anon": TestClient(app), "clock": clock, "recorder": recorder}


@pytest.fixture()
def server(tmp_path: Path, images_root: Path, clock: Clock, monkeypatch):
    """האפליקציה המלאה + לקוחות מחוברים כ-admin וכ-deploy.

    השולח מזויף **גם כאן**, ולא רק ב-`server_with_sender`: סבב מבשיל
    בכמה מסלולים — hello, מבט-על, תחנה — וכל אחד מהם הפעיל `udp-sender`
    אמיתי, על פורט השידור של השרת, שנשאר לרוץ אחרי סוף הריצה (#79).

    ‏`port_holders` מוזרק לריק: בלי `/proc/net/udp` (ווינדוס) המנוע נכשל
    *לפני* השידור המזויף, והטסט מת על כשל סביבתי במקום על ההתנהגות
    שהוא בודק (#312). זה אותו מקור-אמת ש-`free_ports` מחליף ב-test_sender.
    """
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.rounds import sender as sender_module             # noqa: PLC0415
    from test_sender import Recorder                       # noqa: PLC0415

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    bundle = _build_server(tmp_path, images_root, clock, Recorder(block=True))
    yield bundle
    bundle["ctx"].sender.stop()


@pytest.fixture()
def server_with_sender(server: dict):
    """כמו `server`, ועם ה-Recorder בידיים — לבדיקות של השידור עצמו."""
    return server, server["recorder"]


@pytest.fixture(autouse=True)
def _classrooms_restore():
    """הכרעת נדב 11/10: השרת מסרב לכיתות כש-`capabilities.classrooms()` כבוי
    (v1). ‏`setup_classroom` בודק את זרימת v1.2 ולכן מדליק את היכולת לטסט
    הנוכחי — בלי לדרוש מכל אחד מ-40 הקוראים להעביר `monkeypatch` (הם
    מעבירים מילונים שונים). כאן הערך חוזר אחרי כל טסט — ל**מה שהיה לפניו**,
    ולא ל"מה שהיה כש-setup_classroom רץ": ‏autouse מתפרק אחרון, אחרי
    ‏`monkeypatch` של `classrooms_on`, ושחזור יחסי היה משאיר True דולף."""
    from server.core import capabilities                        # noqa: PLC0415
    before = capabilities.CLASSROOMS
    yield
    capabilities.CLASSROOMS = before


@pytest.fixture(autouse=True)
def _host_free_space(request, monkeypatch):
    """‏#1737: השרת שומר ‏10GiB פנויים למערכת (‏`disk_reserve.reserve()`), והם
    נמדדים על הדיסק **של המארח**. ב-Testrunner יש 3.5–7GB פנויים, ולכן כל
    קליטה, העלאה, העברה ומשיכה בחבילה נדחתה "אין מקום" (rc6, ‏5a9a0cf4) — טסט
    שנכשל לפי מצב הדיסק של המכונה שמריצה אותו אינו בודק את הקוד.

    כאן הרזרבה היא 0, כמו לפני #1737 — חוץ מטסט שמסומן `system_reserve`: שם
    היא הערך האמיתי, והטסט מזייף את המקום הפנוי בעצמו. הקוד לא משתנה; רק
    הקלט של הבדיקה (‏`reserve()` נקרא בזמן הקריאה בדיוק בשביל זה)."""
    if request.node.get_closest_marker("system_reserve"):
        return
    from server.storage import disk_reserve                        # noqa: PLC0415
    monkeypatch.setattr(disk_reserve, "reserve", lambda: 0)


@pytest.fixture()
def classrooms_on(monkeypatch):
    """v1.2 לטסט אחד: כיתות דלוקות בשרת (‏`capabilities.CLASSROOMS`)."""
    from server.core import capabilities                        # noqa: PLC0415
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)


def setup_classroom(server: dict, expected: int = 2) -> dict:
    """קבוצת כיתה עם שתי מכונות רשומות; מחזיר מזהים שימושיים.

    מדליק את הכיתות (v1.2) עד סוף הטסט — ראה ‏`_classrooms_restore`."""
    from server.core import capabilities                        # noqa: PLC0415
    capabilities.CLASSROOMS = True
    admin = server["admin"]
    assert admin.post(
        "/api/console/groups",
        json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"},
    ).status_code == 200
    result = admin.post(
        "/api/console/machines/import",
        json={
            "group_id": "grp_LAB1",
            "text": "b4:2e:99:07:1a:c4 05\nB4-2E-99-07-1A-C5, 6\n",
        },
    ).json()
    assert result["saved"] == 2 and not result["rejected"]
    # ‏#880: ברירת המחדל של v1 היא "הפצה לכיתות כבויה", וסבב כיתה מהסוכן
    # (`POST /api/v1/agent/sessions`) מסורב ב-409. בדיקה שרושמת כיתה
    # בודקת את זרימת v2 — ומדליקה את המתג במפורש, כמו שהמפעיל היה עושה.
    # הבדיקות של המתג עצמו מכבות אותו חזרה.
    assert admin.post(
        "/api/console/settings", json={"class_deploy_enabled": "true"}
    ).status_code == 200
    return {
        "group": "grp_LAB1",
        "mac1": "b4:2e:99:07:1a:c4",
        "mac2": "b4:2e:99:07:1a:c5",
        "expected": expected,
    }


def hello_body(mac: str, disk_bytes: int = 256060514304) -> dict:
    """גוף hello מינימלי-תקני (ממשק 2)."""
    return {
        "schema": 1,
        "mac": mac,
        "all_macs": [mac],
        "ip": "10.44.12.187",
        "hostname_current": None,
        "uuid": "4C4C4544-0037",
        "firmware": "uefi",
        "secure_boot": True,
        "agent_version": "0.1.0",
        "memory_bytes": 8589934592,
        "disks": [
            {
                "dev": "sda", "size_bytes": disk_bytes, "model": "Test SSD",
                "serial": "S1", "removable": False, "scheme": "gpt", "has_data": True,
            }
        ],
    }


def room_slots(conn, drawers: dict[str, list[str]],
               size_bytes: int | None = 256060514304) -> list[dict]:
    """‏#1410: סבב חדר נפתח רק על בחירה מפורשת של מגירות שהמשכפל כבר דיווח.

    רושם את `drawers` (‏mac → serials, ‏serial i בחריץ i+1) כדיווח ה-hello
    האחרון של כל מכונה — אותה צורה ש-`cloner_hello` שולח — ומחזיר את
    ‏`target_slots` שבוחר את כולן. ‏`size_bytes=None` הוא כונן שלא דיווח
    גודל. ה-hello האמיתי שהטסט שולח אחר כך דורס את הדיווח הזה כרגיל.
    """
    for mac, serials in drawers.items():
        disks = [
            {"dev": f"sd{chr(ord('a') + i)}", "model": "Drawer SSD",
             "serial": serial, "removable": False, "scheme": "gpt",
             "has_data": False, "port": i + 1,
             **({"size_bytes": size_bytes} if size_bytes is not None else {})}
            for i, serial in enumerate(serials)
        ]
        conn.execute(
            "INSERT INTO net_devices (mac, disks_json) VALUES (?, ?) "
            "ON CONFLICT (mac) DO UPDATE SET disks_json = excluded.disks_json",
            (mac, json.dumps(disks)))
    conn.commit()
    return [{"mac": mac, "ports": list(range(1, len(serials) + 1))}
            for mac, serials in drawers.items()]
