"""#2855: החזרה (#56) של שינוי על כרטיס שלא היה מנוהל לפני השינוי.

בשרת העבודה (v0.91.10, 11/11) ההחזרה של enp2s0 — כרטיס ההפצה, בלי קובץ ב-
`interfaces.d` — מחקה את הקובץ ואז הריצה `ifup enp2s0` על כרטיס ש-ifupdown כבר
לא הכיר: "ifup: unknown interface enp2s0", כשל בכל פעם, והכתובת 20.0.0.1 לא
בהכרח ירדה. כאן: ifdown כשה-stanza עוד קיים, מחיקה, flush בלי ifup, וההצלחה
נקבעת רק מקריאה חוזרת (עיקרון 5). שום hook כאן אינו נוגע במכונה.
"""

from __future__ import annotations

from pathlib import Path

from server.net import netcfg_host, netcfg_rollback
from server.net.netcfg_rollback import Pending

NOW = 1_000_000.0


def marker(**over) -> Pending:
    data = dict(interface="enp2s0", deadline=NOW + 60, armed_at="2026-11-11T11:05:11",
                boot="boot-A", files=[{"name": "enp2s0", "text": None}],
                resolv=None, setting='{"mode": "manual"}')
    return Pending(**{**data, **over})


def machine(**over) -> dict:
    """כרטיס ההפצה אחרי השינוי: קובץ סטטי ו-20.0.0.1/24 על הכרטיס."""
    m = {"confs": {"enp2s0": "iface enp2s0 inet static\n    address 20.0.0.1\n"},
         "addresses": {"enp2s0": ["20.0.0.1/24"]}, "calls": [],
         "readable": True, "flush_clears": True, "main_defines": False}
    m.update(over)
    return m


def hooks(m: dict) -> dict:
    def write_conf(name, text, root=None):
        m["calls"].append(("write", name, text))
        if text is None:
            m["confs"].pop(name, None)
        else:
            m["confs"][name] = text
        return None

    def down(name):
        # ifdown אמיתי יודע מה להוריד רק כשה-stanza עוד בקובץ.
        m["calls"].append(("down", name, name in m["confs"]))
        return None

    def release(name):
        m["calls"].append(("release", name))
        if m["flush_clears"]:
            m["addresses"].pop(name, None)
        return None

    def state():
        if not m["readable"]:
            return netcfg_host.LiveState(False, reason="ip addr: לא רץ")
        return netcfg_host.LiveState(True, {k: list(v) for k, v in m["addresses"].items()})

    return {
        "netcfg_write_conf": write_conf,
        "netcfg_write_resolv": lambda text, path=None: None,
        "netcfg_apply": lambda name: m["calls"].append(("apply", name)) or (
            f"{name}: ifup נכשל (קוד 1): ifup: unknown interface {name}"),
        "netcfg_down": down,
        "netcfg_release": release,
        "netcfg_main_defines": lambda name: m["main_defines"],
        "netcfg_state": state,
    }


def roll(tmp_path: Path, m: dict, **over) -> list[str]:
    netcfg_rollback.write_pending(tmp_path, marker(**over))
    assert netcfg_rollback.run_once(tmp_path, hooks(m), now=NOW + 61,
                                    boot="boot-A") == "expired"
    crumbs = netcfg_rollback.read_crumbs(tmp_path)
    assert len(crumbs) == 1
    return crumbs[0]["errors"]


def test_an_unmanaged_card_goes_back_to_unmanaged_without_ifup(tmp_path: Path):
    """הרצף של שרת העבודה: הקובץ נמחק, ואין ifup על כרטיס ש-ifupdown לא מכיר."""
    m = machine()
    errors = roll(tmp_path, m)
    assert errors == []
    assert "enp2s0" not in m["confs"]
    assert ("apply", "enp2s0") not in m["calls"]
    assert m["addresses"] == {}, "the address the change put there came off"


def test_ifdown_runs_while_the_stanza_is_still_there(tmp_path: Path):
    m = machine()
    roll(tmp_path, m)
    names = [c[0] for c in m["calls"]]
    assert names == ["down", "write", "release"]
    assert m["calls"][0] == ("down", "enp2s0", True)


def test_an_address_that_stayed_is_a_failure_by_name(tmp_path: Path):
    """flush שלא הוריד — הכרטיס עדיין נושא את הכתובת: כשל, לא "הוחזר"."""
    m = machine(flush_clears=False)
    errors = roll(tmp_path, m)
    assert len(errors) == 1 and "20.0.0.1/24" in errors[0]


def test_a_readback_that_did_not_run_is_not_success(tmp_path: Path):
    m = machine(readable=False)
    errors = roll(tmp_path, m)
    assert len(errors) == 1 and "לא נקרא" in errors[0] and "ip addr" in errors[0]


def test_a_card_defined_in_the_main_file_is_still_brought_up(tmp_path: Path):
    """כרטיס שמוגדר ב-`/etc/network/interfaces` עצמו — ifupdown מכיר אותו גם
    בלי הקובץ שלנו, וההחזרה היא ifup כמו קודם. None (לא נקרא) אינו "לא"."""
    for defined in (True, None):
        m = machine(main_defines=defined)
        roll(tmp_path / str(defined), m)
        assert ("apply", "enp2s0") in m["calls"]
        assert not any(c[0] == "release" for c in m["calls"])


def test_a_managed_card_is_untouched_by_this(tmp_path: Path):
    """כרטיס שהיה לו קובץ — אותו מסלול כמו לפני #2855: כתיבה, ואז ifup."""
    m = machine()
    m["calls"].clear()
    netcfg_rollback.write_pending(
        tmp_path, marker(files=[{"name": "enp2s0", "text": "old\n"}]))
    netcfg_rollback.run_once(tmp_path, hooks(m), now=NOW + 61, boot="boot-A")
    assert [c[0] for c in m["calls"]] == ["write", "apply"]


def test_release_never_runs_ifup(monkeypatch):
    """הפקודות עצמן (מדומות): עצירת יחידת ה-ifup ו-flush — ושום ifup."""
    ran = []
    monkeypatch.setattr(netcfg_host, "_run", lambda cmd, timeout=40: (ran.append(cmd), (0, ""))[1])
    assert netcfg_host.release_interface("enp2s0") is None
    assert ran == [["systemctl", "stop", "imagesrv-ifup-enp2s0.service"],
                   ["ip", "-4", "addr", "flush", "dev", "enp2s0"]]
    monkeypatch.setattr(netcfg_host, "_run", lambda cmd, timeout=40: (1, "Cannot find device"))
    assert "flush" in netcfg_host.release_interface("enp2s0")
