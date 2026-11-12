"""מחשב לא רשום ברשת ההפצה: "המחשב הזה לא רשום", והמחשב נשאר עליו (‏#1802).

הכרעת נדב 19/10: מחשב שאינו רשום, בווילן ההפצה בלבד, מגיע לסוכן ורואה את
המסך. נמדד על HP חדש (‏5c:60:ba:3e:b3:22): ‏dnsmasq נתן כתובת ולא קובץ אתחול
(‏`dhcp-boot=...tag:known`, ‏#141), והמסך לא הגיע לאף מסלול.

**הכרעת נדב 04/11:** המחשב עולה לגואי, רואה את המסך עם MAC ו-IP, **ונשאר עליו**.
‏**F2 = הפעלה מחדש בלבד** — הקושחה עולה שוב מהרשת והמחשב חוזר לאותו מסך; אין
אתחול מהדיסק דרך השרת ואין כתיבה לקושחה. ההגדרה "מחשב לא רשום: עלייה אוטומטית
מהדיסק אחרי N שניות" (כבויה כברירת מחדל) חלה **על UEFI בלבד** — ב-BIOS לעולם
לא, בגלל משכפלים לא רשומים (#323, ‏#345).

חמש שכבות, וכל אחת נבדקת כאן מהצד שלה:

* **dnsmasq** — בכרטיס ההפצה בלבד `dhcp-boot` אינו דורש `tag:known`.
* **השרת** — ההגדרה כבויה: המסך בכל אתחול. דלוקה: המסך פעם אחת בחלון, ובתוך
  החלון `unregistered_autoboot`. מחוץ לווילן, או בכל ספק, כמו היום.
* **GRUB** — ערך הסוכן בשורת קרנל נקייה (עיקרון 2); בחלון — רק `efi` בוחר בדיסק,
  ‏BIOS שוב בסוכן. בשום קובץ אין `chainloader +1` (‏#323, ‏#345).
* **הסוכן** — `ui_unknown` נשאר על המסך; ‏F2 = `reboot -f`; הספירה רק ב-UEFI.
* **הגואי** — ‏F2 לוחץ על כפתור שהתווית שלו מתחילה ב-"F2 ", לא מחליף ערכת נושא.

בקרה שלילית: ראו גוף ה-PR — כל קובצי הייצור של ה-PR הוחזרו יחד ל-origin/main.
"""

from __future__ import annotations

import shlex
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom                        # noqa: E402
from test_failure_screen_1569 import (GUI_BUILD, _build_gui, _count_color,  # noqa: E402
                                      _png, _png_rgb, _token, run_agent, values)

from boot.grub_menu import AGENT, GrubConfig, LOCAL, decide, render, render_bootstrap  # noqa: E402
from server.net import deploy_net, dhcp                                     # noqa: E402

REPO = Path(__file__).resolve().parents[1]

#: ה-HP מה-Issue — לא רשום.
STRANGER = "5c:60:ba:3e:b3:22"
#: כתובת ההפצה שאיתה conftest יוצר את השרת, ורגל אחרת של אותו שרת.
VLAN = "http://10.44.12.10:8080"
OFF_VLAN = "http://10.10.10.8:8080"
CFG = GrubConfig(server_base=VLAN)
F2_LABEL = "F2 — הפעלה מחדש"
SETTING = "unregistered_autoboot_seconds"
EFI_DEFAULT = 'if [ "$grub_platform" = "efi" ]; then set default=local; fi'


def menu(server, mac: str, base: str | None = VLAN) -> str:
    url = f"/boot/menu?mac={mac}" if base is None else f"{base}/boot/menu?mac={mac}"
    response = server["anon"].get(url)
    assert response.status_code == 200
    return response.text


def hello(server, mac: str, base: str | None = VLAN, **extra) -> dict:
    url = "/api/v1/agent/hello" if base is None else f"{base}/api/v1/agent/hello"
    response = server["anon"].post(url, json={**hello_body(mac), "joining": False, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def setting(server, value: str) -> None:
    assert server["admin"].post("/api/console/settings", json={SETTING: value}).status_code == 200


def decision(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("# decision: "))


def linux_args(text: str) -> list[str]:
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("if linux "))
    return [word for word in line.split() if word.startswith("imagesrv.")]


def age_mark(server, mac: str, minutes: int) -> None:
    """מזיז את ההגשה אחורה בזמן — כמו מכונה שחוזרת אחרי N דקות."""
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    conn = server["ctx"].conn
    conn.execute("UPDATE boot_attempts SET last_at = ?, first_at = ? WHERE mac = ?", (at, at, mac))
    conn.commit()


# --- GRUB ------------------------------------------------------------------------------


def test_the_flag_boots_the_agent_with_a_clean_kernel_line():
    answer = {"schema": 1, "known": False, "role": "unknown", "task": None, "session": None,
              "unregistered_screen": True}
    assert decide(answer).action == AGENT and decide(answer).code == "unregistered-screen"
    text = render(answer, CFG)
    assert "set default=imagesrv" in text and "set timeout=0" in text
    assert EFI_DEFAULT not in text                                     # המסך בשתי הפלטפורמות
    # עיקרון 2: רק הכתובת. בלי mode, בלי MAC, בלי שום דבר מהתשובה
    assert linux_args(text) == ["imagesrv.server=http://10.44.12.10:8080"]
    assert "unregistered" not in text.split("# decision: ")[1].split("\n", 1)[1]
    assert "--id local" in text                                        # הדיסק נשאר כנפילה
    text.encode("ascii")


@pytest.mark.parametrize("key", ["unregistered_screen", "unregistered_autoboot"])
@pytest.mark.parametrize("flag", [None, False, "true", 1, "yes"])
def test_anything_but_true_is_today_local_disk(key, flag):
    """בשני המצבים: רק `True` פותח. כל ערך אחר — ‏`timeout=0` לדיסק, כמו היום."""
    answer = {"schema": 1, "known": False, "task": None, "session": None}
    if flag is not None:
        answer[key] = flag
    assert decide(answer).action == LOCAL and decide(answer).code == "unregistered"


def test_the_autoboot_file_boots_the_disk_on_uefi_only():
    """ההגדרה דלוקה, בחלון: אותו קובץ סוכן, ושורה אחת — רק `efi` בוחר בדיסק.
    ‏BIOS נשאר על `imagesrv`, כלומר שוב המסך (‏#345)."""
    answer = {"schema": 1, "known": False, "unregistered_autoboot": True, "unregistered_screen": True}
    assert (decide(answer).action, decide(answer).code) == (AGENT, "unregistered-autoboot")
    text = render(answer, CFG)
    head = text[:text.index("function ")]
    assert head.index("set default=imagesrv") < head.index(EFI_DEFAULT), head
    assert "set timeout=0" in head
    assert "--id imagesrv" in text and "--id local" in text
    assert linux_args(text) == ["imagesrv.server=http://10.44.12.10:8080"]
    text.encode("ascii")


@pytest.mark.parametrize("answer", [
    {"schema": 1, "known": False},
    {"schema": 1, "known": False, "unregistered_screen": True},
    {"schema": 1, "known": False, "unregistered_autoboot": True},
    {"schema": 1, "known": True, "role": "classroom", "task": None, "session": None},
    {"schema": 1, "known": True, "role": "cloner", "task": None, "session": None},
    {"schema": 1, "known": True, "role": "teacher", "task": None, "session": None},
    {"schema": 9},
], ids=["unregistered", "screen", "autoboot", "classroom", "cloner", "menu", "bad-schema"])
def test_no_file_can_start_a_bios_disk(answer):
    """בשני המצבים: ‏BIOS = משכפל (#323, ‏#345) — בכל קובץ, גם בקובץ הקבוע, ‏`try_local`
    ב-BIOS הוא העצירה, ואין `chainloader +1`."""
    for text in (render(answer, CFG), render_bootstrap(CFG)):
        assert "chainloader +1" not in text and "chain_legacy" not in text
        if "function try_local" in text:
            assert "Nothing will be started." in text


# --- השרת, דרך /boot/menu כמו GRUB --------------------------------------------------------


def test_the_screen_comes_on_every_boot(server):
    """נדב 04/11: בלי ההגדרה, כל אתחול — שוב המסך, לא הדיסק (F2 = הפעלה מחדש)."""
    for _ in range(3):
        text = menu(server, STRANGER)
        assert decision(text) == "# decision: unregistered-screen", text
        assert "set default=imagesrv" in text and EFI_DEFAULT not in text
        assert linux_args(text) == ["imagesrv.server=http://10.44.12.10:8080"]
        assert STRANGER not in text.split('menuentry "ImageSrv"')[1]   # ה-MAC אינו בערך הסוכן
        text.encode("ascii")
    # ‏#1802 דרישה 5: היומן נשאר `unknown_mac`
    events = [r["event"] for r in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE detail LIKE ?", (f"{STRANGER}%",))]
    assert "unknown_mac" in events


def test_no_hello_can_send_it_to_the_disk(server):
    """אין יותר מסלול F2 דרך השרת: שדה כזה ב-hello אינו מקבל תשובה ואינו משנה דבר."""
    menu(server, STRANGER)
    assert "unregistered_boot" not in hello(server, STRANGER, unregistered_boot="local")
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"


def test_with_the_setting_the_screen_comes_once_per_window(server):
    setting(server, "30")
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"
    inside = menu(server, STRANGER)                                    # אחרי 30 ש' והפעלה מחדש
    assert decision(inside) == "# decision: unregistered-autoboot" and EFI_DEFAULT in inside
    age_mark(server, STRANGER, 11)                                     # 30 ש' + 10 דק' עברו
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"


def test_the_window_covers_the_seconds_of_the_setting(server):
    """N = 600: עשר דקות על המסך — הבקשה שאחריהן עדיין בחלון (N + 600)."""
    setting(server, "600")
    menu(server, STRANGER)
    age_mark(server, STRANGER, 15)
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-autoboot"
    age_mark(server, STRANGER, 21)
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"


def test_turning_the_setting_off_brings_the_screen_back_at_once(server):
    setting(server, "30")
    menu(server, STRANGER)
    setting(server, "0")
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"


@pytest.mark.parametrize("base", [OFF_VLAN, None], ids=["other-local-address", "no-evidence"])
def test_off_the_deploy_vlan_or_in_doubt_nothing_changes(server, base):
    """מחוץ לווילן — וגם ב-TestClient, שה-scope שלו אינו כתובת (ספק) — התשובה
    של היום, בלי לרשום הגשה. ‏CLAUDE.md "תרחישי QA קריטיים" (#140, ‏#17): ערך
    אחד, ‏`timeout=0`, בלי ImageSrv."""
    setting(server, "30")
    for _ in range(3):
        text = menu(server, STRANGER, base)
        assert decision(text) == "# decision: unregistered"
        assert text.count("menuentry ") == 1 and "set timeout=0" in text
        assert "--id imagesrv" not in text and "vmlinuz" not in text
    assert server["ctx"].conn.execute(
        "SELECT 1 FROM boot_attempts WHERE mac = ?", (STRANGER,)).fetchone() is None


def test_a_registered_machine_is_unchanged(server):
    """בשני המצבים: ההחלטה של מכונה רשומה זהה בווילן ובלי ראיה, ואין לה סימון."""
    setting(server, "30")
    ids = setup_classroom(server)
    on_vlan, plain = menu(server, ids["mac1"]), menu(server, ids["mac1"], None)
    assert decision(on_vlan) == decision(plain)
    assert decision(on_vlan) not in ("# decision: unregistered-screen", "# decision: unregistered-autoboot")
    row = server["ctx"].conn.execute(
        "SELECT context FROM boot_attempts WHERE mac = ?", (ids["mac1"],)).fetchone()
    assert row is None or not row["context"].startswith("unregistered-")
    assert SETTING not in hello(server, ids["mac1"])


def test_the_agent_hello_is_not_an_agent_loop(server):
    """ה-hello שאחרי המסך: ‏`known: false`, בלי דגלי התפריט — והשרת עצמו שלח
    אותו לסוכן, ולכן אין "לולאת סוכן" (‏#112). מכונה זרה שהגיעה לסוכן **בלי**
    שהמסך הוגש לה עדיין נספרת, כמו היום."""
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"
    answer = hello(server, STRANGER)
    assert answer["known"] is False
    assert "unregistered_screen" not in answer and "unregistered_autoboot" not in answer
    conn = server["ctx"].conn
    assert conn.execute("SELECT 1 FROM agent_loops WHERE mac = ?", (STRANGER,)).fetchone() is None
    other = "5c:60:ba:3e:b3:23"
    server["anon"].post("/api/v1/agent/hello", json=hello_body(other))
    assert conn.execute("SELECT hits FROM agent_loops WHERE mac = ?", (other,)).fetchone()["hits"] == 1


# --- ההגדרה בקונסולה ---------------------------------------------------------------------


def test_the_setting_is_off_by_default_and_reaches_the_hello(server):
    admin = server["admin"]
    assert admin.get("/api/console/settings").json()[SETTING] == "0"
    assert hello(server, STRANGER)[SETTING] == 0                      # כבוי = 0, לא חסר
    setting(server, "30")
    assert admin.get("/api/console/settings").json()[SETTING] == "30"
    assert hello(server, STRANGER)[SETTING] == 30


@pytest.mark.parametrize("bad", ["601", "-1", "030", "abc", "", True, 1.5])
def test_the_setting_refuses_what_is_not_0_to_600(server, bad):
    admin = server["admin"]
    r = admin.post("/api/console/settings", json={SETTING: bad})
    # הנוסח שנדב אישר (04/11), מילה במילה
    assert r.status_code == 400 and r.json()["detail"] == "הערך חייב להיות בין 0 ל-600 שניות", r.text
    assert admin.get("/api/console/settings").json()[SETTING] == "0"


def test_a_corrupt_stored_setting_is_off(server):
    from server.db import set_setting
    set_setting(server["ctx"].conn, SETTING, "forever")
    assert server["admin"].get("/api/console/settings").json()[SETTING] == "0"
    assert hello(server, STRANGER)[SETTING] == 0
    menu(server, STRANGER)
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"   # כבוי = בלי חלון


# --- dnsmasq ---------------------------------------------------------------------------

GOOD = dict(enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
            netmask="255.255.255.0", gateway="10.44.9.1", lease="12h", server_ip="10.44.9.10")
CLASS = dict(GOOD, range_start="10.44.20.50", range_end="10.44.20.200",
             gateway="10.44.20.1", server_ip="10.44.20.10")


def _rendered(tmp_path, images_root, clock, source: str, interface: str | None) -> str:
    from server.console import dhcp as console_dhcp
    from server.app import create_app
    from server.db import set_setting
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock)
    conn = app.state.ctx.conn
    set_setting(conn, dhcp.SETTING_PREFIX + "eth1", dhcp.InterfaceConfig("eth1", **GOOD).to_json())
    set_setting(conn, dhcp.SETTING_PREFIX + "eth2", dhcp.InterfaceConfig("eth2", **CLASS).to_json())
    url = "http://10.44.9.10:8080" if source != "none" else None
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState(source, interface, url),
        agent_port=8080, tftp_root=Path("/srv/tftp"), repo_dir=REPO)
    text, _proxy = console_dhcp.render_dnsmasq(app.state.ctx, deploy)
    conn.close()
    return text


def _boot_lines(text: str, nic: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.startswith(f"dhcp-boot=tag:if-{nic},")]
    assert len(lines) == 2, (nic, text)          # BIOS + UEFI
    return lines


@pytest.mark.parametrize("source", ["cli", "console"])
def test_only_the_deploy_nic_serves_the_boot_file_to_an_unregistered_machine(
        tmp_path, images_root, clock, source):
    text = _rendered(tmp_path, images_root, clock, source, "eth1")
    assert _boot_lines(text, "eth1") == [
        "dhcp-boot=tag:if-eth1,tag:bios,grub/i386-pc/core.0,,10.44.9.10",
        "dhcp-boot=tag:if-eth1,tag:efi-x86_64,bootx64.efi,,10.44.9.10"]
    # כרטיס אחר שמחלק כתובות (וילן אחר) — כמו ב-#141
    assert all(",tag:known," in ln for ln in _boot_lines(text, "eth2"))


@pytest.mark.parametrize("source,interface", [("none", None), ("cli", None)])
def test_without_a_named_deploy_nic_every_boot_line_still_needs_known(
        tmp_path, images_root, clock, source, interface):
    """בשני המצבים: רשת הפצה שלא הוגדרה, או יחידה בלי ‎--interface‏ — אין
    ראיה איזה כרטיס הוא ההפצה, ולכן כמו היום (עיקרון 1)."""
    text = _rendered(tmp_path, images_root, clock, source, interface)
    for nic in ("eth1", "eth2"):
        assert all(",tag:known," in ln for ln in _boot_lines(text, nic))


# --- הסוכן ------------------------------------------------------------------------------

#: ‏json_get של run_agent עונה null לכל דבר; כאן — ההגדרה מתשובת ה-hello (AUTO).
#: ‏http_post_json מתועד: F2 אינו פונה לשרת.
STUBS = (
    'json_get() { case "$2" in .unregistered_autoboot_seconds) echo "${AUTO:-null}" ;; *) echo null ;; esac; }; '
    'SERVER=http://srv; http_post_json() { echo "$1" >> "$RUN_DIR/posted"; }; RESP="$RUN_DIR/response.json"; '
)


def _ui_unknown(tmp_path, *, env: str = "", press_after: float | None = None,
                hold: float = 0, gui: bool = False, uefi: bool = False) -> tuple[dict, float]:
    """הסוכן האמיתי: ‏ui_unknown, ו"לחיצה" על F2 (‏fail.action reboot, כמו הגואי)
    אחרי `press_after` שניות. ‏`hold` > 0 = רץ ברקע, ונבדק שהוא עדיין שם אחריו.
    ‏`uefi` — ‏`$SYSROOT/sys/firmware/efi` קיים (‏detect_firmware)."""
    if uefi:
        (tmp_path / "sys" / "sys" / "firmware" / "efi").mkdir(parents=True, exist_ok=True)
    press = "" if press_after is None else (
        f'( sleep {press_after}; mkdir -p "$RUN_DIR/gui"; echo reboot > "$RUN_DIR/gui/fail.action" ) & ')
    if hold:
        body = (f'{press}ui_unknown {shlex.quote(STRANGER)} & _uu=$!; sleep {hold}; '
                'if kill -0 "$_uu" 2>/dev/null; then echo STILL-ON-SCREEN; kill "$_uu"; fi')
    else:
        body = f'{press}ui_unknown {shlex.quote(STRANGER)}'
    started = time.time()
    r = run_agent(tmp_path, STUBS + body, env=env, gui=gui)
    return r, started


def _log(r) -> str:
    path = r["run"] / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.parametrize("uefi", [False, True], ids=["bios", "uefi"])
def test_ui_unknown_stays_on_the_screen(tmp_path, uefi):
    """נדב 04/11: המחשב נשאר על המסך — 4 שניות (יותר מ-UNKNOWN_POLL_S) ועדיין שם."""
    r, _ = _ui_unknown(tmp_path, hold=4, gui=True, uefi=uefi)
    assert "STILL-ON-SCREEN" in r["out"] and "TEST-REBOOT" not in r["out"], r["out"]
    recs = r["recs"]
    assert values(recs, "fail") == ["inf|0|המחשב הזה לא רשום|הקונסולה קיבלה התראה על מחשב חדש|unknown"]
    assert values(recs, "fail_btn") == [f"reboot|{F2_LABEL}"]
    assert values(recs, "fail_at") == [], "ספירה לאחור כשההגדרה כבויה"
    assert values(recs, "fail_why") == ["השרת לא מכיר את המחשב הזה, ולכן אין לו משימה."]
    assert values(recs, "fail_step") == ["כדי לצרף את המחשב לשרת: רשמו אותו בקונסולה לפי כתובת ה-MAC."]
    assert values(recs, "fail_ctx") == ["כתובת MAC|5C:60:BA:3E:B3:22", "כתובת IP|10.44.0.82",
                                        "מצב|לא רשום בשרת"]
    assert "[agent] ui_unknown: MAC not registered" in values(recs, "fail_code")
    # בלי קיוסק: גואי לתצוגה בלבד על קובץ #1548, עם אותן רשומות
    args = (r["run"] / "gui.args").read_text(encoding="utf-8")
    assert "/gui/failed.state" in args and "--mac" in args, args
    assert values(r["seen"], "fail") == values(recs, "fail")
    assert "This computer is not registered (5c:60:ba:3e:b3:22)." in r["out"]   # ה-tty


@pytest.mark.parametrize("uefi", [False, True], ids=["bios", "uefi"])
def test_f2_only_restarts(tmp_path, uefi):
    """F2 = הפעלה מחדש בלבד: ‏`reboot -f`, בלי בקשה לשרת ובלי לגעת בקושחה."""
    r, _ = _ui_unknown(tmp_path, press_after=1.5, uefi=uefi)
    assert "TEST-REBOOT: MAC not registered (F2)" in r["out"], r["out"]
    assert not (r["run"] / "posted").exists(), "F2 פנה לשרת"
    assert "efibootmgr" not in _log(r)


def test_the_setting_counts_down_on_uefi(tmp_path):
    r, started = _ui_unknown(tmp_path, env="AUTO=2", uefi=True)
    assert "TEST-REBOOT: MAC not registered (2s)" in r["out"], r["out"]
    assert values(r["recs"], "fail_step")[0] == "אין צורך לעשות דבר — האתחול ימשיך בעוד 2 שניות."
    assert "אתחול|מהדיסק המקומי" in values(r["recs"], "fail_ctx")
    at = int(values(r["recs"], "fail_at")[0])
    assert 2 <= at - int(started) <= 2 + 10, (at, started)   # טעינת הספריות ב-bash בווינדוס לוקחת שניות
    assert not (r["run"] / "posted").exists()


def test_the_setting_never_counts_down_on_bios(tmp_path):
    """נדב 04/11: ב-BIOS לעולם אין עלייה אוטומטית — משכפל לא רשום (#345)."""
    r, _ = _ui_unknown(tmp_path, env="AUTO=2", hold=5)
    assert "STILL-ON-SCREEN" in r["out"] and "TEST-REBOOT" not in r["out"], r["out"]
    assert values(r["recs"], "fail_at") == []
    assert "Legacy BIOS -- never an automatic boot (#345); the setting 2s is for UEFI" in _log(r)


@pytest.mark.parametrize("auto", ["null", "601", "abc", "-5"])
def test_a_missing_or_bad_setting_is_off(tmp_path, auto):
    r, _ = _ui_unknown(tmp_path, env=f"AUTO={auto}", hold=4, uefi=True)
    assert "STILL-ON-SCREEN" in r["out"] and values(r["recs"], "fail_at") == [], r["out"]
    assert f"unregistered_autoboot_seconds '{auto}' is not 0-600 -- off" in _log(r)


def test_the_agent_main_loop_hands_an_unknown_answer_to_ui_unknown():
    agent = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    branch = agent[agent.index("        unknown)"):]
    branch = branch[:branch.index(";;")]
    assert 'ui_unknown "$MAC"' in branch and "sleep 10" not in branch, branch
    assert branch.index("ui_unknown") < branch.index("die_local")      # הנפילה רק אם reboot לא קרה


# --- הגואי הנייטיבי ------------------------------------------------------------------------


def _src(name: str) -> str:
    return (REPO / "native-gui" / "src" / name).read_text(encoding="utf-8")


def test_the_native_gui_knows_the_info_tone():
    assert 's->fail_info = !strcmp(f[0], "inf")' in _src("state.c")
    failure = _src("screens_failure.c")
    assert "עולה מהדיסק בעוד" in failure and "עולה מהדיסק המקומי" in failure
    assert "s->fail_warn, s->fail_info" in failure


def test_f2_presses_the_f2_button_not_the_theme():
    """F2 בכל מסך מחליף ערכת נושא (#1499). במסך כשל עם כפתור שהתווית שלו
    מתחילה ב-"F2 " — הוא הכפתור (ה-action שלו), ונבדק **לפני** ערכת הנושא.
    ‏Enter אינו לוחץ עליו."""
    main = _src("main.c")
    handle = main[main.index("static int handle(App *a, const Event *e"):]
    press = handle.index('power_drop(a, "fail.action", a->st.fail_btns[fail_f2_btn(a)].action)')
    assert press < handle.index("toggle_theme(a);")
    assert "e->key == KEYSYM_F2 && fail_f2_btn(a) >= 0" in handle[:press]
    assert "e->key == KEYSYM_ENTER && fail_f2_btn(a) != 0) b = 0;" in handle
    helper = main[main.index("static int fail_f2_btn("):]
    helper = helper[:helper.index("\n}\n")]
    assert "a->screen != SCREEN_FAILURE" in helper and 'strncmp(a->st.fail_btns[i].label, "F2 ", 3)' in helper


def test_the_unknown_screen_says_f2_and_not_enter():
    screens = _src("screens.c")
    keys = screens[screens.index("case SCREEN_FAILURE:{static char k[96];"):]
    keys = keys[:keys.index("\n")]
    assert keys.index('"F2 "') < keys.index('"Enter  %s"'), keys
    failure = _src("screens_failure.c")
    assert 'primary && strncmp(a->st.fail_btns[i].label, "F2 ", 3)' in failure
    word = failure[failure.index("const char *failure_status_word("):]
    word = word[:word.index("\n}\n")]
    assert word.index("s->fail_at > 0") < word.index('if (s->fail_info) { *dot = t->indigo; return "לא רשום בשרת"; }')


@GUI_BUILD
def test_the_unknown_screen_renders_a_blue_badge_not_a_red_one(tmp_path):
    binary = _build_gui(tmp_path)
    r = run_agent(tmp_path, f'ui_unknown_records {shlex.quote(STRANGER)} 0 > "$RUN_DIR/fail.state"')
    state = tmp_path / "state.txt"
    # ‏`message=` כמו ב-`_state_for_png` (#1569): ‏`--png` מצייר את כל הכרטיסים,
    # וכרטיס ההודעה בלי הודעה מנותב לתפריט ונכשל לפני כרטיס הכשל
    state.write_text((r["run"] / "fail.state").read_text(encoding="utf-8")
                     + "message=x|y\nhello_age=4\nhello_rc=200\n", encoding="utf-8", newline="\n")
    _png(binary, tmp_path / "card", state)
    w, h, ch, px = _png_rgb(tmp_path / "card-failure-light.png")
    assert _count_color(px, ch, _token("--clr-action-soft"), tol=4) > 1500, "אין תג כחול"
    assert _count_color(px, ch, _token("--clr-danger-soft"), tol=4) < 1500, "תג אדום על מסך שאינו כישלון"
    assert _count_color(px, ch, _token("--clr-action"), tol=4) > 3000, "אין כפתור F2 ראשי"



def _header_pill_pixels(tmp_path, binary, record_edit):
    """Pixels of the role pill (white at .14 over the header, 37,51,61 -> ~68,80,88) in the header band."""
    r = run_agent(tmp_path, f'ui_unknown_records {shlex.quote(STRANGER)} 0 > "$RUN_DIR/fail.state"')
    text = record_edit((r["run"] / "fail.state").read_text(encoding="utf-8"))
    state = tmp_path / "state.txt"
    state.write_text(text + "message=x|y\nhello_age=4\nhello_rc=200\n", encoding="utf-8", newline="\n")
    _png(binary, tmp_path / "card", state)
    w, h, ch, px = _png_rgb(tmp_path / "card-failure-light.png")
    band = px[: 50 * w * ch]   # the header is 48 px high
    return _count_color(band, ch, (68, 80, 88), tol=3)


@GUI_BUILD
def test_unregistered_machine_header_draws_no_role_pill_2879(tmp_path):
    """#2879: an unregistered machine has no known role -- the header must not say "מחשב בנייה"."""
    binary = _build_gui(tmp_path)
    none = _header_pill_pixels(tmp_path, binary, lambda t: t)
    # the same screen with the role field empty (what the agent wrote before) draws the pill
    old = _header_pill_pixels(tmp_path, binary, lambda t: t.replace("|unknown\n", "|\n", 1))
    assert old > 500, f"the control (no role field) draws no pill: {old}"
    assert none < old / 10, f"role pill drawn for an unregistered machine: {none} px (control {old})"
