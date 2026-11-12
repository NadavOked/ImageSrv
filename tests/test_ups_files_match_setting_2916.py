"""‏#2916 §4 — הגדרת ה-UPS וקובצי NUT מתעדכנים יחד, או חוזרים יחד.

**התרחיש:** usb → none, ו-`systemctl disable --now nut-server` נכשל. ‏`apply` כבר כתב
`nut.conf` עם `MODE=none`, ‏`RuntimeError` מונע את `set_settings` — ב-DB `ups_mode=usb`
ובקובץ `MODE=none`. ‏`UpsMonitor` ממשיך לפי ה-DB ולא משווה לקובץ; אחרי אתחול NUT לא
עולה, וה-UPS "לא נקרא" בלי שאיש שינה כלום.

**התיקון:** כשל בהחלה מחזיר את הקבצים והשירות למצב הקודם, ורק אז נזרקת השגיאה (שהקונסולה
אומרת "ולא נשמרה" — עכשיו זה נכון גם לגבי הקבצים). ‏`net` ו-`none` אינם נוגעים בקבצים.
"""

from __future__ import annotations

import pytest

from server.db import connect, set_settings
from server.system import ups

OL = {"ups.status": "OL", "battery.charge": "100"}


class Host:
    """מארח מזויף עם **מצב**: הקבצים שעל הדיסק והאם השירות רץ — לא רק יומן קריאות.
    ‏`fail` = אילו קריאות (בשם) נכשלות, ובכמה פעמים."""

    def __init__(self, files=None, fail=()) -> None:
        self.files: dict[str, str] = dict(files or {})
        self.running = "nut.conf" in self.files and "MODE=standalone" in self.files["nut.conf"]
        self.fail = list(fail)
        self.calls: list[str] = []

    def _maybe(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            self.fail.remove(name)
            raise RuntimeError(f"systemctl {name}: Access denied")

    def hooks(self) -> dict:
        def write(files):
            self._maybe("write")
            self.files.update(files)

        def start():
            self._maybe("start_usb")
            self.running = True

        def stop():
            self._maybe("stop_usb")
            self.running = False
        return {"write": write, "start_usb": start, "stop_usb": stop,
                "read": lambda target: OL, "sleep": lambda s: None}


USB_FILES = ups.render_usb()


def test_a_failed_leave_of_usb_puts_the_files_back_with_the_setting(tmp_path):
    db = connect(tmp_path / "x.db")
    set_settings(db, {ups.MODE: "usb"})
    host = Host(files=USB_FILES, fail=["stop_usb"])
    with pytest.raises(RuntimeError, match="stop_usb"):
        ups.apply(db, "none", "", host.hooks(), "noc")
    assert ups.configured(db)[0] == "usb"                           # the setting did not move
    assert "MODE=standalone" in host.files["nut.conf"], host.files["nut.conf"]   # and neither did the file
    assert host.running is True                                    # nor the service
    assert host.files == USB_FILES


def test_a_failed_start_of_usb_puts_the_files_back_with_the_setting(tmp_path):
    db = connect(tmp_path / "x.db")
    host = Host(fail=["start_usb"])
    with pytest.raises(RuntimeError, match="start_usb"):
        ups.apply(db, "usb", "", host.hooks(), "noc")
    assert ups.configured(db)[0] == "none"
    assert "MODE=none" in host.files["nut.conf"], host.files           # not "standalone" with ups_mode=none
    assert host.running is False


def test_when_the_way_back_fails_too_the_error_says_so(tmp_path):
    db = connect(tmp_path / "x.db")
    set_settings(db, {ups.MODE: "usb"})
    host = Host(files=USB_FILES, fail=["stop_usb", "start_usb"])
    with pytest.raises(RuntimeError) as caught:
        ups.apply(db, "none", "", host.hooks(), "noc")
    assert "stop_usb" in str(caught.value) and "start_usb" in str(caught.value)
    assert "שחזור המצב הקודם" in str(caught.value)
    assert ups.configured(db)[0] == "usb"


def test_a_successful_switch_still_saves_and_keeps_the_new_files(tmp_path):
    """השומר מפני תיקון-יתר: בלי כשל — הקבצים החדשים נשארים וההגדרה נשמרת."""
    db = connect(tmp_path / "x.db")
    set_settings(db, {ups.MODE: "usb"})
    host = Host(files=USB_FILES)
    ups.apply(db, "none", "", host.hooks(), "noc")
    assert ups.configured(db)[0] == "none"
    assert "MODE=none" in host.files["nut.conf"] and host.running is False
    ups.apply(db, "usb", "", host.hooks(), "noc")
    assert ups.configured(db)[0] == "usb" and host.files == USB_FILES and host.running is True


def test_net_and_none_never_touch_the_files(tmp_path):
    db = connect(tmp_path / "x.db")
    host = Host(fail=["write", "stop_usb", "start_usb"])
    ups.apply(db, "net", "rack@10.0.0.5", host.hooks(), "noc")
    assert host.calls == [] and ups.configured(db)[0] == "net"
