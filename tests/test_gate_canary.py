"""‏#2121: הקנרית של שער המעבדה — טסט שחייב להיכשל במצב בקרה.

ריצה "ירוקה" שווה משהו רק אם המכונה **מסוגלת** לדווח כישלון: מבצע שלא
נכשל מעולם אינו מכשיר מדידה. ‏`tools/git/lab-run-tests.sh --canary` מריץ את
החבילה עם `IMAGECTL_GATE_CANARY=1`, והשער (`tools/git/lab_gate.py`) מאשר
שהריצה נכשלה **ובגלל הטסט הזה** — גם במקביל, כש-worker אחר מריץ אותו.

בכל ריצה אחרת הוא עובר, ונספר בשער השלמות כמו כל טסט אחר.
"""
from __future__ import annotations

import os


def test_the_gate_canary_fails_only_when_asked():
    assert os.environ.get("IMAGECTL_GATE_CANARY") != "1", (
        "canary (#2121): IMAGECTL_GATE_CANARY=1 -- this failure is the point; "
        "the gate must see it as the reason the run is red"
    )
