"""#3666 (אפיק #3653): מה שהסוכן והשרת מוסיפים למצב שהגואי קורא -- פסק לכל מגירה, מאותה טבלה.

הגואי כבר לא מחשב את צבע הדיסק מהעובדות שבשורת ``lc_disk=`` / ``cdisk=`` / ``room_drawer=``: הוא לוקח את הפסק שנמסר לו (``lc_rule=`` /
``cdisk_rule=`` מהסוכן, `agent/lib/smart/diskrule.sh`; ``room_drawer_rule=`` מהשרת). טסט שמזין לגואי מצב כתוב-ביד כותב בו את העובדות --
וכאן נוספות הרשומות שהיו מגיעות לפיהן, מאותה טבלה בדיוק (`server.disks.disk_rule`, ש-`tests/test_disk_rule_agent_3666.py` מחזיק שווה
לסוכן על כל צירוף).

**טסט שרוצה לבדוק גואי בלי פסק** (עיקרון 5: לא נבדק, לעולם לא ירוק) כותב את המצב ישירות, בלי המסייע הזה, או כותב בו רשומת פסק כלשהי
(מצב שכבר נושא רשומות פסק של סוג אחד נשאר כמו שהוא לסוג הזה).
"""

from __future__ import annotations

from pathlib import Path

from server.disks import disk_rule


def _smart(raw: str) -> str:
    return "ok" if raw == "passed" else raw if raw in ("ok", "warn", "fail", "failed_last") else "unchecked"


def _verdict(smart: str, mark: str = "", words: str = "", slow: bool = False) -> dict:
    return disk_rule.judge({"smart": _smart(smart), "write_incomplete": mark == "incomplete",
                            "sata_issue": any(w in words.split() for w in ("link_reset", "drawer_gone")), "write_slow": slow})


def _he(v: dict) -> str:
    return disk_rule.REASON_HE.get(v["reason"], "") if v["reason"] else ""


def _fields(v: dict) -> str:
    return f"{v['color']}|{int(v['source'])}|{int(v['target'])}|{v['reason'] or ''}|{_he(v)}"


def with_rules(state: str) -> str:
    """``state`` + the verdict records its facts would have come with: a ``lc_rule=`` after the last ``lc_disk=``, a ``cdisk_rule=`` after the
    last ``cdisk=`` (lc_disk: dev 2, smart 6, mark 16, words 19 -- cdisk: dev 2, smart 5, mark 12, words 17, write check 18), and a
    ``room_drawer_rule=`` after each ``room_drawer=`` (machine 1, serial 3, smart 6 -- the server knows more than the record says, so only
    SMART). A kind that already has any record in the state is left exactly as it is."""
    lines = state.splitlines()
    have = {k: any(l.startswith(k + "=") for l in lines) for k in ("lc_rule", "cdisk_rule", "room_drawer_rule")}
    out, lc, cd = [], [], []
    for line in lines:
        key, _, val = line.partition("=")
        f = val.split("|")
        out.append(line)
        if key == "lc_disk" and len(f) >= 6:
            lc.append(f"lc_rule={f[1]}|" + _fields(_verdict(f[5], f[15] if len(f) > 15 else "", f[18] if len(f) > 18 else "")))
        elif key == "cdisk" and len(f) >= 5:
            cd.append(f"cdisk_rule={f[1]}|" + _fields(_verdict(f[4], f[11] if len(f) > 11 else "", f[16] if len(f) > 16 else "",
                                                               len(f) > 17 and f[17] == "slow")))
        elif key == "room_drawer" and len(f) >= 6 and not have["room_drawer_rule"] and f[2]:
            v = _verdict(f[5])   # the room's order is color|TARGET|SOURCE (the agent's records are color|source|target)
            out.append(f"room_drawer_rule={f[0]}|{f[2]}|{v['color']}|{int(v['target'])}|{int(v['source'])}|{v['reason'] or ''}|{_he(v)}")
    for name, records, kind in (("lc_disk=", lc, "lc_rule"), ("cdisk=", cd, "cdisk_rule")):
        if records and not have[kind]:
            last = max(i for i, l in enumerate(out) if l.startswith(name))
            out[last + 1:last + 1] = records
    return "\n".join(out) + ("\n" if state.endswith("\n") else "")


def ruled_file(path: Path) -> Path:
    """`with_rules` applied to a state file in place (for a helper that has already written it)."""
    path.write_text(with_rules(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    return path
