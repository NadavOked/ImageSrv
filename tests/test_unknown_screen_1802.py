"""מחשב לא רשום ברשת ההפצה: "המחשב הזה לא רשום" 10 שניות, ואז הדיסק (‏#1802).

הכרעת נדב 19/10. נמדד על HP חדש (‏5c:60:ba:3e:b3:22): ‏dnsmasq נתן כתובת
ולא קובץ אתחול (‏`dhcp-boot=...tag:known`, ‏#141), והקושחה נפלה לדיסק בלי
מסך — המסך היה בקוד (`ui_unknown`) ושום מסלול לא הגיע אליו.

ארבע שכבות, וכל אחת נבדקת כאן מהצד שלה:

* **dnsmasq** — בכרטיס ההפצה בלבד `dhcp-boot` אינו דורש `tag:known`; כל
  כרטיס אחר כמו ב-#141 (‏`console_dhcp.render_dnsmasq`, הנתיב שכותב את הקובץ).
* **השרת** — בקשת תפריט על כתובת ההפצה מקבלת את הדגל **פעם אחת בחלון**;
  מחוץ לווילן, או בכל ספק, כמו היום. מכונה רשומה לא משתנה.
* **GRUB** — הדגל = ערך הסוכן הרגיל, שורת קרנל נקייה (עיקרון 2).
* **הסוכן** — `ui_unknown` מצייר את מקרה 8 במוקאפ, סופר 10 שניות ומאתחל.

בקרה שלילית: בלי קובצי הייצור של ה-Issue כל טסט כאן נכשל על התנהגות —
‏`tag:known` על כרטיס ההפצה, ‏`decision: unregistered` על הווילן, ‏`ui_unknown`
שחוזר בלי לאתחל — חוץ מהשומרים שמסומנים "בשני המצבים".
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

from boot.grub_menu import AGENT, GrubConfig, LOCAL, decide, render      # noqa: E402
from server.net import deploy_net, dhcp                                     # noqa: E402

REPO = Path(__file__).resolve().parents[1]

#: ה-HP מה-Issue — לא רשום.
STRANGER = "5c:60:ba:3e:b3:22"
#: כתובת ההפצה שאיתה conftest יוצר את השרת, ורגל אחרת של אותו שרת.
VLAN = "http://10.44.12.10:8080"
OFF_VLAN = "http://10.10.10.8:8080"
CFG = GrubConfig(server_base=VLAN)


def menu(server, mac: str, base: str | None = VLAN) -> str:
    url = f"/boot/menu?mac={mac}" if base is None else f"{base}/boot/menu?mac={mac}"
    response = server["anon"].get(url)
    assert response.status_code == 200
    return response.text


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
    # עיקרון 2: רק הכתובת. בלי mode, בלי MAC, בלי שום דבר מהתשובה
    assert linux_args(text) == ["imagesrv.server=http://10.44.12.10:8080"]
    assert "unregistered" not in text.split("# decision: ")[1].split("\n", 1)[1]
    assert "--id local" in text                                        # הדיסק נשאר כנפילה
    text.encode("ascii")


@pytest.mark.parametrize("flag", [None, False, "true", 1, "yes"])
def test_anything_but_true_is_today_local_disk(flag):
    """בשני המצבים: רק `True` פותח. כל ערך אחר — ‏`timeout=0` לדיסק, כמו היום."""
    answer = {"schema": 1, "known": False, "task": None, "session": None}
    if flag is not None:
        answer["unregistered_screen"] = flag
    assert decide(answer).action == LOCAL and decide(answer).code == "unregistered"


# --- השרת, דרך /boot/menu כמו GRUB --------------------------------------------------------


def test_an_unregistered_machine_on_the_deploy_vlan_gets_the_agent_once(server):
    first = menu(server, STRANGER)
    assert decision(first) == "# decision: unregistered-screen", first
    assert "set default=imagesrv" in first
    assert linux_args(first) == ["imagesrv.server=http://10.44.12.10:8080"]
    assert STRANGER not in first.split('menuentry "ImageSrv"')[1]     # ה-MAC אינו בערך הסוכן
    first.encode("ascii")
    # אין לולאה: האתחול הבא של אותו MAC (אחרי ה-reboot של ui_unknown) — לדיסק, מיד
    second = menu(server, STRANGER)
    assert decision(second) == "# decision: unregistered"
    assert "set default=local" in second and "set timeout=0" in second
    assert second.count("menuentry ") == 1 and "vmlinuz" not in second
    # ‏#1802 דרישה 5: היומן נשאר `unknown_mac`
    events = [r["event"] for r in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE detail LIKE ?", (f"{STRANGER}%",))]
    assert "unknown_mac" in events


def test_the_window_ends_and_a_later_boot_sees_the_screen_again(server):
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"
    age_mark(server, STRANGER, 9)
    assert decision(menu(server, STRANGER)) == "# decision: unregistered"       # עדיין בחלון
    age_mark(server, STRANGER, 11)
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"
    assert decision(menu(server, STRANGER)) == "# decision: unregistered"


@pytest.mark.parametrize("base", [OFF_VLAN, None], ids=["other-local-address", "no-evidence"])
def test_off_the_deploy_vlan_or_in_doubt_nothing_changes(server, base):
    """מחוץ לווילן — וגם ב-TestClient, שה-scope שלו אינו כתובת (ספק) —
    התשובה של היום. ובלי לצרוך את החלון: הבקשה הבאה בווילן עדיין מקבלת מסך."""
    for _ in range(3):
        text = menu(server, STRANGER, base)
        assert decision(text) == "# decision: unregistered"
        # ‏CLAUDE.md "תרחישי QA קריטיים" (#140, ‏#17): ערך אחד, ‏`timeout=0`, בלי ImageSrv
        assert text.count("menuentry ") == 1 and "set timeout=0" in text
        assert "--id imagesrv" not in text and "vmlinuz" not in text
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"


def test_a_registered_machine_is_unchanged(server):
    """בשני המצבים: ההחלטה של מכונה רשומה זהה בווילן ובלי ראיה, ואין לה סימון."""
    ids = setup_classroom(server)
    on_vlan, plain = menu(server, ids["mac1"]), menu(server, ids["mac1"], None)
    assert decision(on_vlan) == decision(plain) != "# decision: unregistered-screen"
    row = server["ctx"].conn.execute(
        "SELECT context FROM boot_attempts WHERE mac = ?", (ids["mac1"],)).fetchone()
    assert row is None or row["context"] != "unregistered-screen"


def test_the_agent_hello_does_not_carry_the_flag_and_is_not_an_agent_loop(server):
    """ה-hello שאחרי המסך: ‏`known: false` כמו תמיד, בלי הדגל — והשרת עצמו
    שלח אותו לסוכן, ולכן אין "לולאת סוכן" (‏#112). מכונה זרה שהגיעה לסוכן
    **בלי** שהמסך הוגש לה עדיין נספרת, כמו היום."""
    assert decision(menu(server, STRANGER)) == "# decision: unregistered-screen"
    answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(STRANGER)).json()
    assert answer["known"] is False and "unregistered_screen" not in answer
    conn = server["ctx"].conn
    assert conn.execute("SELECT 1 FROM agent_loops WHERE mac = ?", (STRANGER,)).fetchone() is None
    other = "5c:60:ba:3e:b3:23"
    server["anon"].post("/api/v1/agent/hello", json=hello_body(other))
    assert conn.execute("SELECT hits FROM agent_loops WHERE mac = ?", (other,)).fetchone()["hits"] == 1


# --- dnsmasq ---------------------------------------------------------------------------

GOOD = dict(enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
            netmask="255.255.255.0", gateway="10.44.9.1", lease="12h", server_ip="10.44.9.10")
CLASS = dict(GOOD, range_start="10.44.20.50", range_end="10.44.20.200",
             gateway="10.44.20.1", server_ip="10.44.20.10")


def _rendered(tmp_path, images_root, clock, source: str, interface: str | None) -> str:
    from server.console import console_dhcp
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


def _ui_unknown(tmp_path, env: str = "", gui: bool = False) -> tuple[dict, float, float]:
    started = time.time()
    r = run_agent(tmp_path, f"ui_unknown {shlex.quote(STRANGER)}", env=env, gui=gui)
    return r, started, time.time() - started


def test_ui_unknown_draws_case_8_counts_down_and_reboots(tmp_path):
    r, started, took = _ui_unknown(tmp_path, env="UNKNOWN_SHOW_S=2", gui=True)
    assert "TEST-REBOOT: MAC not registered" in r["out"], r["out"]
    assert "RETURNED" not in r["out"], "ui_unknown חזר בלי לאתחל"
    assert 2 <= took < 30, took
    recs = r["recs"]
    assert values(recs, "fail") == ["inf|0|המחשב הזה לא רשום|הקונסולה קיבלה התראה על מחשב חדש|"]
    assert values(recs, "fail_ctx") == ["כתובת MAC|5C:60:BA:3E:B3:22", "כתובת IP|10.44.0.82",
                                        "מצב|לא רשום בשרת", "אתחול|מהדיסק המקומי"]
    assert values(recs, "fail_step")[0] == "אין צורך לעשות דבר — האתחול ימשיך בעוד 2 שניות."
    # ‏fail_at = רגע הציור + 2; טעינת הספריות ב-bash לפניו לוקחת שניות בווינדוס
    at = int(values(recs, "fail_at")[0])
    assert 2 <= at - int(started) <= 2 + 10, (at, started)
    assert "[agent] ui_unknown: MAC not registered" in values(recs, "fail_code")
    # בלי קיוסק: גואי לתצוגה בלבד על קובץ #1548, עם אותם רשומות
    args = (r["run"] / "gui.args").read_text(encoding="utf-8")
    assert "/gui/failed.state" in args and "--mac" in args, args
    assert values(r["seen"], "fail") == values(recs, "fail")
    assert "This computer is not registered (5c:60:ba:3e:b3:22)." in r["out"]   # ה-tty, בלי גואי


@pytest.mark.parametrize("value", ["", "9999"], ids=["default", "capped"])
def test_ui_unknown_waits_ten_seconds_by_default_and_never_longer_than_its_cap(tmp_path, value):
    """עשר שניות (הכרעת נדב). ערך מוגזם אינו מחזיק מכונה על המסך — חוזר ל-10."""
    r, started, took = _ui_unknown(tmp_path, env=f"UNKNOWN_SHOW_S={value}" if value else "")
    assert "TEST-REBOOT: MAC not registered" in r["out"], r["out"]
    assert 10 <= took < 30, took
    assert values(r["recs"], "fail_step")[0] == "אין צורך לעשות דבר — האתחול ימשיך בעוד 10 שניות."
    assert 10 <= int(values(r["recs"], "fail_at")[0]) - int(started) <= 10 + 10


def test_the_agent_main_loop_hands_an_unknown_answer_to_ui_unknown():
    agent = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    branch = agent[agent.index("        unknown)"):]
    branch = branch[:branch.index(";;")]
    assert 'ui_unknown "$MAC"' in branch and "sleep 10" not in branch, branch
    assert branch.index("ui_unknown") < branch.index("die_local")      # הנפילה רק אם reboot לא קרה


# --- הגואי הנייטיבי ------------------------------------------------------------------------


def test_the_native_gui_knows_the_info_tone():
    state = (REPO / "native-gui" / "src" / "state.c").read_text(encoding="utf-8")
    assert 's->fail_info = !strcmp(f[0], "inf")' in state
    failure = (REPO / "native-gui" / "src" / "screens_failure.c").read_text(encoding="utf-8")
    assert "עולה מהדיסק בעוד" in failure and "עולה מהדיסק המקומי" in failure
    assert "s->fail_warn, s->fail_info" in failure


@GUI_BUILD
def test_the_unknown_screen_renders_a_blue_badge_not_a_red_one(tmp_path):
    binary = _build_gui(tmp_path)
    r, _, _ = _ui_unknown(tmp_path, env="UNKNOWN_SHOW_S=1")
    state = tmp_path / "state.txt"
    # ‏`message=` כמו ב-`_state_for_png` (#1569): ‏`--png` מצייר את כל הכרטיסים,
    # וכרטיס ההודעה בלי הודעה מנותב לתפריט ונכשל לפני כרטיס הכשל
    state.write_text((r["run"] / "fail.state").read_text(encoding="utf-8")
                     + "message=x|y\nhello_age=4\nhello_rc=200\n", encoding="utf-8", newline="\n")
    _png(binary, tmp_path / "card", state)
    w, h, ch, px = _png_rgb(tmp_path / "card-failure-light.png")
    assert _count_color(px, ch, _token("--clr-action-soft"), tol=4) > 1500, "אין תג כחול"
    assert _count_color(px, ch, _token("--clr-danger-soft"), tol=4) < 1500, "תג אדום על מסך שאינו כישלון"
