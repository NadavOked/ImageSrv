"""‏#1977: שם שאינו חוקי ב-Windows — הסירוב אומר מה השם ומה הכלל.

הסוכן כבר סירב לשם כזה לפני כתיבת ה-hive
(‏`test_agent.test_a_bad_hostname_is_refused_before_touching_the_disk`), אבל
ההודעה שהגיעה ליעד ולקונסולה הייתה "שם המחשב אינו תקין" — בלי השם ובלי
הכלל. שם שנרשם לפני #1997 ("עמדה 3") נוחת כאן, ומי שקורא את שורת המכונה
צריך לדעת מה לשנות.

מ-#1997 השרת אוכף את אותו כלל ברישום (`server/hostname_rule.py`,
‏`test_hostname_rule_1997` — שם גם ההשוואה בין הסוכן לשרת).
"""

from __future__ import annotations

import json

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))


def refuse(tmp_path, name: str) -> dict:
    run = tmp_path / "run"
    run.mkdir()
    out = sh(
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/hostname.sh; '
        f'write_hostname sda /nonexistent.json {name!r} || true'
    )
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False and result["code"] == "bad_hostname", result
    return result


@pytest.mark.parametrize("name", ["BUILD-עמדה3", "PULL-PC 3", "LAB_05"])
def test_the_refusal_names_the_machine_and_the_rule(tmp_path, name):
    error = refuse(tmp_path, name)["error"]
    assert name in error
    assert "אותיות אנגליות, ספרות ומקף" in error


def test_a_long_name_is_named_with_its_limit(tmp_path):
    error = refuse(tmp_path, "COMPUTERLAB204-05")["error"]
    assert "COMPUTERLAB204-05" in error and "15" in error


def test_a_name_with_a_quote_still_makes_valid_json(tmp_path):
    """השם נכנס להודעה — ולכן הוא עובר json_escape, לא printf גולמי."""
    assert 'LAB"1' in refuse(tmp_path, 'LAB"1')["error"]
