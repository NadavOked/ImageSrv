"""ההחזרה האוטומטית של תצורת רשת שלא אושרה (‏#56) — ההגנה מנעילה עצמית.

זו הפרוסה שמצילה את המפעיל מעצמו: שינוי כתובת על הכרטיס שדרכו הוא
מחובר מנתק אותו, ואז אין דרך לבטל. הדרישה הקשה היא ש**ההחזרה תשרוד גם
מוות של השרת** — ולכן רוב הקובץ הזה בודק את הזרוע *בלי* השרת: מריץ
את `run_once` ישירות, כפי שיחידת ה-systemd מריצה אותה, בלי FastAPI
ובלי DB.

שלושה תרחישים שאסור לקפל לאחד:

* **פקיעה** בזמן שהשרת חי — הזרוע מחזירה, והשרת רואה את הפירור.
* **קריסה** — השרת מת, הזרוע ממשיכה לפי הטיימר.
* **אתחול** — סמן פתוח עם boot_id אחר, ולכן החזרה מיידית בלי לחכות
  לפקיעה בכלל.

ובכולם: **היעדר ניתוק אינו אישור.** בלי "אני עדיין רואה את הקונסולה"
מפורש — ההגדרה הקודמת חוזרת.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.net import netcfg, netcfg_host, netcfg_rollback
from server.net.netcfg_rollback import Pending

from test_server_netcfg import (           # noqa: F401 — הפיקסטורה מיובאת בשמה
    CONF, ROUTE, STATIC, confirm, journal_events, net_server, put,
)

BOOT_A, BOOT_B = "boot-A", "boot-B"
NOW = 1_000_000.0


def marker(**over) -> Pending:
    data = dict(interface="eth1", deadline=NOW + 60, armed_at="2026-08-29T10:00:00",
                boot=BOOT_A, files=[{"name": "eth1", "text": "old text\n"}],
                resolv="nameserver 10.44.0.5\n", setting='{"mode": "manual"}')
    return Pending(**{**data, **over})


# --- ההחלטה, טהורה -----------------------------------------------------------


def test_nothing_pending_means_nothing_to_do():
    assert netcfg_rollback.decide(None, NOW, BOOT_A) is None


def test_inside_the_window_on_the_same_boot_nothing_happens():
    assert netcfg_rollback.decide(marker(), NOW + 59, BOOT_A) is None


def test_the_window_closing_is_what_rolls_it_back():
    """היעדר ניתוק אינו אישור — הזמן שעבר בלי אישור הוא הראיה."""
    assert netcfg_rollback.decide(marker(), NOW + 60, BOOT_A) == "expired"


def test_an_open_marker_on_a_new_boot_rolls_back_immediately():
    """שינוי שהפיל את הרשת מפיל לעיתים גם את השרת, ואז המכונה מאותחלת.
    סמן פתוח אחרי אתחול = השינוי לא אושר, ואין מה לחכות לפקיעה."""
    assert netcfg_rollback.decide(marker(), NOW + 1, BOOT_B) == "boot"
    # והסיבה שמדווחת היא האתחול, גם כשגם הזמן עבר.
    assert netcfg_rollback.decide(marker(), NOW + 999, BOOT_B) == "boot"


def test_a_machine_without_a_boot_id_still_expires():
    """בלי `/proc` (פיתוח, ווינדוס) נשארת הפקיעה. חצי הגנה, לא אפס."""
    assert netcfg_rollback.decide(marker(boot=""), NOW + 1, "") is None
    assert netcfg_rollback.decide(marker(boot=""), NOW + 61, "") == "expired"


# --- הזרוע, בלי שרת ובלי DB --------------------------------------------------


def arm_hooks(store: dict) -> dict:
    """‏hooks שכותבים למילון במקום לרשת. הזרוע רצה כאן בדיוק כפי שהיא
    רצה תחת systemd, פרט לכך ששום `ifup` אינו קורה."""
    def write_conf(name, text, root=None):
        if text is None:
            store["confs"].pop(name, None)
        else:
            store["confs"][name] = text
        return None
    return {
        "netcfg_write_conf": write_conf,
        "netcfg_write_resolv": lambda text, path=None: store.__setitem__(
            "resolv", text),
        "netcfg_apply": lambda name: store["applied"].append(name) or None,
    }


@pytest.fixture()
def store() -> dict:
    return {"confs": {"eth1": "new text\n"}, "resolv": "nameserver 8.8.8.8\n",
            "applied": []}


def test_the_previous_file_comes_back_word_for_word(tmp_path: Path, store):
    netcfg_rollback.write_pending(tmp_path, marker())
    reason = netcfg_rollback.run_once(tmp_path, arm_hooks(store),
                                      now=NOW + 61, boot=BOOT_A)
    assert reason == "expired"
    assert store["confs"]["eth1"] == "old text\n"
    assert store["resolv"] == "nameserver 10.44.0.5\n"
    assert store["applied"] == ["eth1"]
    assert netcfg_rollback.read_pending(tmp_path) is None


def test_a_card_that_had_no_file_gets_none_back(tmp_path: Path, store):
    """‏`text: null` פירושו "לא היה קובץ" — וההחזרה מוחקת, ולא משאירה
    קובץ ריק שנראה מנוהל."""
    netcfg_rollback.write_pending(
        tmp_path, marker(files=[{"name": "eth1", "text": None}]))
    netcfg_rollback.run_once(tmp_path, arm_hooks(store), now=NOW + 61,
                             boot=BOOT_A)
    assert "eth1" not in store["confs"]


def test_a_confirmed_change_is_never_touched_by_a_later_tick(tmp_path: Path, store):
    netcfg_rollback.write_pending(tmp_path, marker())
    netcfg_rollback.clear_pending(tmp_path)                 # זה מה שאישור עושה
    assert netcfg_rollback.run_once(tmp_path, arm_hooks(store),
                                    now=NOW + 6000, boot=BOOT_B) is None
    assert store["confs"]["eth1"] == "new text\n"
    assert store["applied"] == []


def test_running_twice_rolls_back_once(tmp_path: Path, store):
    netcfg_rollback.write_pending(tmp_path, marker())
    assert netcfg_rollback.run_once(tmp_path, arm_hooks(store), now=NOW + 61,
                                    boot=BOOT_A) == "expired"
    store["confs"]["eth1"] = "someone else wrote this\n"
    assert netcfg_rollback.run_once(tmp_path, arm_hooks(store), now=NOW + 200,
                                    boot=BOOT_A) is None
    assert store["confs"]["eth1"] == "someone else wrote this\n"


def test_the_rollback_leaves_a_crumb_with_its_own_time(tmp_path: Path, store):
    """ההחזרה קורית כשאיש לא מסתכל. אירוע שלא השאיר ראיה שקול
    ללא-קרה — והזמן שנשמר הוא של ההחזרה, לא של הקריאה."""
    netcfg_rollback.write_pending(tmp_path, marker())
    netcfg_rollback.run_once(tmp_path, arm_hooks(store), now=NOW + 61,
                             boot=BOOT_A)
    crumbs = netcfg_rollback.read_crumbs(tmp_path)
    assert len(crumbs) == 1 and crumbs[0]["interface"] == "eth1"
    assert crumbs[0]["reason"] == "expired"
    assert crumbs[0]["at"].startswith("19") or crumbs[0]["at"][:2] == "20"
    assert crumbs[0]["setting"] == '{"mode": "manual"}'


def test_a_marker_that_cannot_be_parsed_is_not_swallowed(tmp_path: Path, store):
    """סמן פגום אינו מפעיל החזרה — אין לנו את הטקסט הקודם, והחזרה
    שמנחשת גרועה מהעדר החזרה. הוא כן מדווח, ולא נשאר בשקט לנצח."""
    netcfg_rollback.pending_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    netcfg_rollback.pending_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert netcfg_rollback.corrupt_pending(tmp_path) is True
    assert netcfg_rollback.run_once(tmp_path, arm_hooks(store), now=NOW,
                                    boot=BOOT_A) is None
    assert store["applied"] == []
    crumbs = netcfg_rollback.read_crumbs(tmp_path)
    assert len(crumbs) == 1 and crumbs[0]["errors"]
    assert netcfg_rollback.read_pending(tmp_path) is None


def test_a_marker_missing_a_field_is_treated_as_unreadable(tmp_path: Path):
    path = netcfg_rollback.pending_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"interface": "eth1"}), encoding="utf-8")
    assert netcfg_rollback.read_pending(tmp_path) is None
    assert netcfg_rollback.corrupt_pending(tmp_path) is True


# --- דרך הקונסולה: החימוש ------------------------------------------------------


def pending_file(server) -> dict | None:
    path = netcfg_rollback.pending_path(server["state_dir"])
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_touching_the_card_the_console_arrives_on_arms_the_rollback(net_server):
    """הקונסולה מגיעה דרך eth0. שינוי עליו הוא בדיוק המקרה שאין ממנו
    דרך חזרה — ולכן הוא חמוש."""
    result = put(net_server, name="eth0", address="10.10.10.9").json()
    assert result["rollback"]["pending"] is True
    assert result["rollback"]["interface"] == "eth0"
    assert result["rollback"]["seconds_left"] == 60
    assert pending_file(net_server)["interface"] == "eth0"


def test_a_change_on_another_card_needs_no_confirmation(net_server):
    result = put(net_server, address="10.44.9.11").json()
    assert result["rollback"]["pending"] is False
    assert pending_file(net_server) is None


def test_a_gateway_or_dns_change_arms_it_on_any_card(net_server):
    """‏#57: שניהם יכולים לנתק את הקונסולה בדיוק כמו שינוי כתובת."""
    assert put(net_server, dns=["9.9.9.9"]).json()["rollback"]["pending"] is True
    confirm(net_server)
    assert put(net_server, gateway="10.44.9.1").json()["rollback"]["pending"] is True


def test_an_address_that_cannot_be_traced_to_a_card_is_treated_as_the_live_one(
        net_server):
    """מי שלא יודע דרך מה הוא מחובר חייב להניח שהוא מנתק את עצמו.
    ‏"לא ידוע" נופל לצד המגן, לא לצד המרשה (עיקרון 5)."""
    net_server["fake"]["local"] = ""
    assert put(net_server, address="10.44.9.11").json()["rollback"]["pending"] is True


def test_the_marker_is_on_disk_before_the_file_is_touched(net_server):
    """סדר הכתיבות הוא ההגנה. מכונה שמתה בין הכתיבה להחלה חייבת
    להשאיר סמן פתוח; סמן שנכתב **אחרי** השינוי משאיר חלון שבו השינוי
    כבר חי ואין מה שיחזיר אותו."""
    put(net_server, name="eth0", address="10.10.10.9")
    assert net_server["fake"]["writes"] == [("eth0", True)]
    # ולהפך: שינוי שאינו דורש חימוש נכתב בלי סמן בכלל.
    confirm(net_server, "eth0")
    put(net_server, address="10.44.9.11")
    assert net_server["fake"]["writes"][-1] == ("eth1", False)


def test_a_change_is_refused_when_the_rollback_arm_is_not_running(net_server):
    """הגנה שלא הוכחה אינה הגנה. בלי הטיימר השינוי הוא חד-כיווני,
    ולכן הוא פשוט לא מבוצע — ולא מבוצע "בזהירות"."""
    net_server["fake"]["timer"] = (False, "inactive")
    response = put(net_server, name="eth0", address="10.10.10.9")
    assert response.status_code == 409
    assert "imagesrv-netrollback.timer" in response.json()["detail"]
    assert net_server["fake"]["confs"] == {}
    assert pending_file(net_server) is None


def test_a_change_that_needs_no_arming_runs_without_the_timer(net_server):
    net_server["fake"]["timer"] = (False, "inactive")
    assert put(net_server, address="10.44.9.11").status_code == 200


def test_a_second_change_while_one_waits_is_refused(net_server):
    put(net_server, name="eth0", address="10.10.10.9")
    response = put(net_server, address="10.44.9.11")
    assert response.status_code == 409
    assert "ממתין לאישור" in response.json()["detail"]


# --- דרך הקונסולה: האישור ------------------------------------------------------


def test_confirming_clears_the_marker_and_keeps_the_change(net_server):
    put(net_server, name="eth0", address="10.10.10.9")
    result = confirm(net_server, "eth0")
    assert result.status_code == 200 and result.json()["ok"] is True
    assert pending_file(net_server) is None
    assert "10.10.10.9" in net_server["fake"]["confs"]["eth0"]
    assert ("net_confirmed", "eth0") in journal_events(net_server)


def test_confirming_after_the_window_closed_does_not_undo_anything(net_server):
    """"אישור אחרי פקיעה אינו מבטל החזרה שכבר קרתה" — הוא נדחה, כי
    ההחזרה כבר קרתה או עומדת לקרות, ו"ביטול" שלה היה משאיר מצב שאיש
    לא בחר בו."""
    put(net_server, name="eth0", address="10.10.10.9")
    net_server["clock"].advance(61)
    response = confirm(net_server, "eth0")
    assert response.status_code == 409
    assert "נסגר" in response.json()["detail"]
    # והזרוע אכן מחזירה, למרות שהמפעיל "אישר".
    assert netcfg_rollback.run_once(
        net_server["state_dir"], net_server["hooks"],
        now=net_server["clock"](), boot="boot-A") == "expired"
    assert "10.10.10.9" not in (net_server["fake"]["confs"].get("eth0") or "")


def test_confirming_when_the_rollback_already_ran_says_so(net_server):
    put(net_server, name="eth0", address="10.10.10.9")
    net_server["clock"].advance(61)
    netcfg_rollback.run_once(net_server["state_dir"], net_server["hooks"],
                             now=net_server["clock"](), boot="boot-A")
    response = confirm(net_server, "eth0")
    assert response.status_code == 409
    assert "כבר הוחזר" in response.json()["detail"]


def test_confirming_the_wrong_card_is_refused(net_server):
    put(net_server, name="eth0", address="10.10.10.9")
    assert confirm(net_server, "eth1").status_code == 409


def test_a_confirmation_whose_marker_survives_is_not_reported_as_confirmed(
        net_server, monkeypatch):
    """‏#291 (התראת code-scanning על ה-``except: pass`` ב-`clear_pending`):
    מחיקת הסמן שנכשלה נבלעת שם, והקונסולה ענתה "אושר — ההגדרה נשארת"
    ורשמה `net_confirmed` — בזמן שהסמן נשאר על הדיסק והזרוע מחזירה את
    השינוי בתום החלון. "אושר" בלי ראיה שהסמן איננו הוא עיקרון 5 בדיוק.
    ה-DCUI כבר בודק את זה (`dcui/actions.confirm_network`)."""
    put(net_server, name="eth0", address="10.10.10.9")
    real_unlink = Path.unlink

    def stuck(self, *args, **kwargs):
        if self.name == netcfg_rollback.PENDING_NAME:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stuck)
    response = confirm(net_server, "eth0")
    assert response.status_code == 500, response.text
    assert "סמן ההחזרה" in response.json()["detail"]
    # ‏#1226 "גמור": ואומר שהשינוי יוחזר — לא רק שהמחיקה נכשלה
    assert "יוחזר בתום החלון" in response.json()["detail"], response.text
    assert pending_file(net_server)["interface"] == "eth0"
    assert ("net_confirmed", "eth0") not in journal_events(net_server)


def test_an_unarmed_change_whose_old_marker_survives_says_so(net_server, monkeypatch):
    """‏#2076: שינוי שאינו חמוש מנקה סמן ישן (רק סמן פגום מגיע לשם — תקין נדחה
    קודם). ‏`clear_pending` בולע כישלון מחיקה; "הוחל" בלי לומר שהסמן נשאר הוא
    עיקרון 5 — אותה ראיה חיובית כמו באישור (#291/#1226)."""
    path = netcfg_rollback.pending_path(net_server["state_dir"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    real_unlink = Path.unlink

    def stuck(self, *args, **kwargs):
        if self.name == netcfg_rollback.PENDING_NAME:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stuck)
    result = put(net_server, address="10.44.9.11").json()
    assert result["rollback"]["pending"] is False                  # לא חמוש: eth1
    assert result["ok"] is False, result
    assert "סמן ההחזרה הישן" in (result["apply_error"] or ""), result
    assert path.exists()
    assert any(e == "net_rollback_marker_stuck" for e, _ in journal_events(net_server))


def test_an_unarmed_change_that_clears_the_old_marker_is_ok(net_server):
    """הצד השני: המחיקה הצליחה — ok, בלי שגיאה ובלי סמן."""
    path = netcfg_rollback.pending_path(net_server["state_dir"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    result = put(net_server, address="10.44.9.11").json()
    assert result["ok"] is True and result["apply_error"] is None, result
    assert not path.exists()


# --- הפירור → שורת יומן --------------------------------------------------------


def events(server, name: str) -> list[str]:
    return [d for e, d in journal_events(server) if e == name]


def test_a_rollback_while_the_server_lives_reaches_the_journal_at_once(net_server):
    put(net_server, name="eth0", address="10.10.10.9")
    net_server["clock"].advance(61)
    netcfg_rollback.run_once(net_server["state_dir"], net_server["hooks"],
                             now=net_server["clock"](), boot="boot-A")
    assert events(net_server, "net_rollback") == []      # עוד לא נקרא
    net_server["admin"].get(CONF)                        # כל קריאת מצב סורקת
    lines = events(net_server, "net_rollback")
    assert len(lines) == 1
    assert lines[0].startswith("eth0 at=") and "reason=expired" in lines[0]


def test_the_crumb_is_read_once_and_never_twice(net_server):
    put(net_server, name="eth0", address="10.10.10.9")
    net_server["clock"].advance(61)
    netcfg_rollback.run_once(net_server["state_dir"], net_server["hooks"],
                             now=net_server["clock"](), boot="boot-A")
    net_server["admin"].get(CONF)
    net_server["admin"].get(CONF)
    assert len(events(net_server, "net_rollback")) == 1


def test_the_database_goes_back_with_the_machine(net_server):
    """הגדרה שמוצגת בקונסולה ואינה זו שעל הכרטיס היא אותו שקר בכיוון
    ההפוך — ולכן הפירור נושא גם את ההגדרה הקודמת."""
    put(net_server, name="eth0", address="10.10.10.9")
    net_server["clock"].advance(61)
    netcfg_rollback.run_once(net_server["state_dir"], net_server["hooks"],
                             now=net_server["clock"](), boot="boot-A")
    net_server["admin"].get(CONF)
    rows = {r["name"]: r for r in net_server["admin"].get(CONF).json()["interfaces"]}
    assert rows["eth0"]["mode"] == netcfg.MODE_MANUAL
    assert rows["eth0"]["address"] == ""


def test_a_rollback_at_boot_reports_the_boot_as_the_reason(net_server):
    """התרחיש שהמשימה קיימת בשבילו: השינוי הפיל את הרשת, השרת מת
    איתה, המכונה אותחלה — והזרוע מחזירה בעלייה, בלי לחכות לפקיעה."""
    put(net_server, name="eth0", address="10.10.10.9")
    assert netcfg_rollback.run_once(
        net_server["state_dir"], net_server["hooks"],
        now=net_server["clock"](), boot="boot-B") == "boot"
    net_server["admin"].get(CONF)
    assert "reason=boot" in events(net_server, "net_rollback")[0]


def test_an_unreadable_crumb_is_reported_and_not_swallowed(net_server):
    path = Path(net_server["state_dir"]) / "2026-08-29T10-00-00.rollback.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    net_server["admin"].get(CONF)
    assert events(net_server, "net_rollback_unreadable") == [""]
    assert not list(Path(net_server["state_dir"]).glob("*.rollback.json"))


def test_a_crumb_left_while_the_server_was_down_is_read_at_startup(
        tmp_path: Path, images_root: Path, clock):
    """זו כל הנקודה של #56: ההחזרה בעלייה רצה **כשהשרת לא רץ**, ולכן
    אין לה חיבור ל-DB. השורה מגיעה ליומן בהפעלה הבאה — עם זמן ההחזרה."""
    from fastapi.testclient import TestClient

    from server.console import users
    from server.app import create_app

    state_dir = tmp_path / "netstate"
    state_dir.mkdir(parents=True)
    (state_dir / "2026-08-29T04-05-06.rollback.json").write_text(json.dumps({
        "schema": 1, "interface": "eth0", "reason": "boot",
        "at": "2026-08-29T04:05:06+03:00", "errors": [], "setting": None,
    }), encoding="utf-8")

    app = create_app(tmp_path / "data", images_root, "http://10.10.10.8:8080",
                     now_fn=clock, netcfg_state_dir=state_dir)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    client = TestClient(app)
    client.post("/api/console/login",
                json={"username": "noc", "password": "admin-pass-123"})
    rows = client.get("/api/console/journal").json()
    line = next(r for r in rows if r["event"] == "net_rollback")
    assert line["label"] == "הוחזרה תצורת רשת קודמת, השינוי לא אושר"
    # הזמן שמדווח הוא של ההחזרה, ולא של הקריאה שקרתה עכשיו.
    assert "2026-08-29 04:05" in line["text"]
    assert "המכונה אותחלה" in line["text"]
    assert "eth0" in line["text"]


# --- היחידות עצמן: המקום שבו ההגנה יכולה להיות נכונה ולא קיימת ----------------

REPO = Path(__file__).resolve().parent.parent
UNIT = (REPO / "deploy" / "imagesrv-netrollback.service").read_text(
    encoding="utf-8")
TIMER = (REPO / "deploy" / "imagesrv-netrollback.timer").read_text(
    encoding="utf-8")
INSTALLER = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")


def test_the_unit_reads_the_marker_where_the_server_writes_it():
    """המלכודת המסוכנת ביותר כאן: נתיב שונה בשני הצדדים. הסמן נכתב
    במקום אחד ונקרא באחר, וזה נראה **בדיוק** כמו הגנה שעובדת."""
    assert f"--state-dir {netcfg_host.STATE_DIR}" in UNIT
    # וזה גם מה שהשרת גוזר מ---data-dir שביחידה שלו.
    server_unit = (REPO / "deploy" / "imagesrv-server.service").read_text(
        encoding="utf-8")
    data_dir = server_unit.split("--data-dir ")[1].split()[0]
    assert netcfg_host.STATE_DIR == f"{data_dir}/netcfg"


def test_the_check_runs_on_the_way_up_and_not_only_on_a_clock():
    """‏OnBootSec=0 הוא מה שתופס את המקרה הגרוע: השינוי הפיל את הרשת,
    השרת נפל איתה, המכונה אותחלה."""
    assert "OnBootSec=0" in TIMER
    assert "OnUnitActiveSec=10s" in TIMER
    # בלי AccuracySec סיסטמד מאחד טיימרים עד דקה, ומכפיל את החלון בשקט.
    assert "AccuracySec=1s" in TIMER


def test_the_unit_may_write_everything_the_rollback_writes():
    paths = next(line for line in UNIT.splitlines()
                 if line.startswith("ReadWritePaths="))
    assert netcfg_host.INTERFACES_DIR in paths
    assert netcfg_host.STATE_DIR.startswith("/var/lib/imagesrv")
    assert "/var/lib/imagesrv" in paths and " /etc" in paths


def test_the_installer_puts_the_guard_on_a_fresh_server():
    """בלי הטיימר הקונסולה חוסמת כל שינוי שיכול לנתק — ומפעיל שנחסם
    בלי להבין למה חוזר לערוך קבצים ב-SSH, כלומר לעבודה שהלשונית הזאת
    נועדה להחליף."""
    assert "imagesrv-netrollback.service" in INSTALLER
    assert "imagesrv-netrollback.timer" in INSTALLER
    assert "systemctl enable --now imagesrv-netrollback.timer" in INSTALLER
