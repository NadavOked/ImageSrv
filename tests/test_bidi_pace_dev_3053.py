"""#3053: כיוון הטקסט בגואי -- "114.7 MB/s" ו-"/dev/sda" נשארים ריצת LTR אחת.

שורת השחזור (`sr_pace_text`) נבדקת התנהגותית: מריצים את הפונקציה ובודקים
את המחרוזת שיוצאת, כולל סמני הכיוון (U+2066 ... U+2069, כמו #3388).
בורר הקליטה הוא C: בדיקת המקור שה-dev עובר דרך `bidi_isolate` לפני שהוא
נכנס לשורה, ו-render של כרטיס `pick` שעובר.
"""
from __future__ import annotations

import subprocess

import pytest

from test_cloner_gui import BASH, GUI, posix

LRI, PDI = "\u2066", "\u2069"


def _pace(rate: str, eta: str = "-1") -> str:
    out = subprocess.run(
        [BASH, "-c", f'. {posix(GUI.parent / "agent")}/lib/gui/srgui.sh; _cg_rate={rate}; _cg_eta={eta}; sr_pace_text'],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.parametrize("rate, eta, said", [
    ("114700000", "-1", f"{LRI}114.7 MB/s{PDI} · הזמן שנותר לא ידוע"),
    ("114700000", "30", f"{LRI}114.7 MB/s{PDI} · פחות מדקה נותרה"),
    ("114700000", "600", f"{LRI}114.7 MB/s{PDI} · כ-10 דק׳ נותרו"),
    ("0", "-1", f"ממתין לזרם מהשרת · {LRI}0 MB/s{PDI}"),
    ("-1", "-1", "מודד את הקצב…"),
])
def test_the_pace_line_isolates_the_number_with_its_unit(rate, eta, said):
    assert _pace(rate, eta) == said


def test_the_capture_picker_isolates_the_device_path():
    src = (GUI / "src" / "screens_capture.c").read_text(encoding="utf-8")
    assert "bidi_isolate(d->dev, dev_i" in src
    # every line of the disk's sub-text takes the isolated copy, never the raw path
    for line in src.splitlines():
        if "snprintf(small" in line:
            assert "d->dev" not in line, line
