"""‏#2469 — תג גבוה שאינו חתום אינו מסתיר את העדכון החתום.

‏``check_update`` בחר את התג הגבוה ביותר בריפו הציבורי ובדק חתימה רק עליו.
מי שיכול לדחוף תג לציבורי (בלי מפתח השחרור) דחף ``v9.9.9`` לא חתום, הכפתור
"החל" נכבה, והעדכון החתום האמיתי לא הוצע כלל. עכשיו: מהגבוה לנמוך, עד תקרה,
וההצעה היא הגבוה ביותר שמצבו ``signed`` בלבד. ‏``unchecked`` אינו ``signed``
— כשל של הבדיקה עצמה אינו הופך להצעה (עיקרון 5).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server import update as update_mod

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
URL = "https://example/pub.git"


def _remote(*tags: str) -> str:
    return "".join(f"x\trefs/tags/{t}\n" for t in tags)


def _check(remote: str, states: dict, current: str = "v0.24.0"):
    """‏``states``: תג → מצב שהמאמת מחזיר, או חריגה שהוא זורק."""
    seen: list[str] = []

    def verify(url, tag, repo_dir):
        seen.append(tag)
        state = states[tag]
        if isinstance(state, Exception):
            raise state
        return {"state": state, "detail": f"{tag}-{state}"}

    hooks = {"describe": lambda _repo: current,
             "ls_remote_tags": lambda _url: (True, remote, ""),
             "verify_tag": verify}
    return update_mod.check_update(hooks, "/repo", URL), seen


def test_an_unsigned_v9_does_not_hide_the_signed_update():
    result, seen = _check(_remote("v0.24.0", "v0.25.0", "v9.9.9"),
                          {"v9.9.9": "unsigned", "v0.25.0": "signed"})
    assert result["latest"] == "v0.25.0", result
    assert result["available"] is True and result["reason"] is None
    assert result["signature"] == {"state": "signed", "detail": "v0.25.0-signed"}
    assert result["skipped"] == [{"tag": "v9.9.9", "state": "unsigned",
                                  "detail": "v9.9.9-unsigned"}]
    assert seen == ["v9.9.9", "v0.25.0"]              # מהגבוה לנמוך, ונעצר בחתום


@pytest.mark.parametrize("failure", ["unchecked", RuntimeError("clone failed")])
def test_a_higher_tag_whose_check_failed_is_not_the_offer(failure):
    result, _ = _check(_remote("v0.25.0", "v9.9.9"), {"v9.9.9": failure, "v0.25.0": "signed"})
    assert result["latest"] == "v0.25.0", result
    assert result["signature"]["state"] == "signed"
    assert [(s["tag"], s["state"]) for s in result["skipped"]] == [("v9.9.9", "unchecked")]


@pytest.mark.parametrize("failure", ["unsigned", "unchecked", RuntimeError("no verifier")])
def test_no_signed_newer_tag_is_no_offer_and_says_so(failure):
    result, _ = _check(_remote("v0.24.0", "v0.25.0"), {"v0.25.0": failure})
    assert result["available"] is False and result["latest"] is None, result
    assert result["reason"] == "no_signed"
    assert "v0.25.0" in result["message"]
    assert "signature" not in result                  # אין הצעה — אין חתימה של הצעה
    assert [s["tag"] for s in result["skipped"]] == ["v0.25.0"]


def test_tags_not_newer_than_the_installed_one_are_never_checked():
    result, seen = _check(_remote("v0.23.0", "v0.24.0", "v0.25.0"),
                          {"v0.25.0": "unsigned"})
    assert seen == ["v0.25.0"]
    assert result["reason"] == "no_signed" and result["latest"] is None


def test_more_unsigned_tags_than_the_cap_is_its_own_state_not_no_update():
    """הסקירה על #2469: תוקף שדוחף יותר תגים לא חתומים מהתקרה מסתיר שוב את
    החתום. זה חייב להיות מצב בשם, עם הודעה למנהל — לא "אין עדכון"."""
    signed_below = "v1.0.0"
    tags = [signed_below] + [f"v9.9.{n}" for n in range(12)]
    states = {t: "unsigned" for t in tags}
    states[signed_below] = "signed"
    result, seen = _check(_remote(*tags), states)
    assert result["available"] is False and result["latest"] is None, result
    assert result["reason"] == "too_many_unsigned", result
    cap = update_mod.MAX_SIGNATURE_CHECKS
    assert 1 < cap < len(tags) - 1
    assert seen == list(reversed(tags))[:cap]
    assert f"יותר מ-{cap} " in result["message"]
    assert f"עוד {len(tags) - cap} " in result["message"]
    assert "דוחף" in result["message"]


def test_exactly_cap_unsigned_tags_are_all_checked_and_are_no_signed():
    cap = update_mod.MAX_SIGNATURE_CHECKS
    tags = [f"v9.9.{n}" for n in range(cap)]
    result, seen = _check(_remote(*tags), {t: "unsigned" for t in tags})
    assert len(seen) == cap
    assert result["reason"] == "no_signed" and "יותר מ-" not in result["message"]


def test_an_unknown_installed_version_still_finds_the_highest_signed():
    result, _ = _check(_remote("v0.25.0", "v9.9.9"),
                       {"v9.9.9": "unsigned", "v0.25.0": "signed"}, current=None)
    assert result["latest"] == "v0.25.0" and result["available"] is True


def test_a_signed_highest_tag_is_offered_as_before_without_a_skipped_list():
    result, seen = _check(_remote("v0.25.0", "v0.26.0"), {"v0.26.0": "signed"})
    assert result == {"current": "v0.24.0", "latest": "v0.26.0", "available": True,
                      "reason": None,
                      "signature": {"state": "signed", "detail": "v0.26.0-signed"}}
    assert seen == ["v0.26.0"]


# --- הקונסולה: התג שדולג מוצג כאזהרה ליד ההצעה ------------------------------


def _js(name: str, js: str) -> str:
    m = re.search(rf"^function {name}\(.*?^\}}", js, re.M | re.S)
    assert m, f"function {name} not found in console.js"
    return m.group(0)


@requires_native(("node", NODE))
def test_the_console_warns_about_a_skipped_higher_tag_next_to_the_offer(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x);
const UI = { note: (k, h) => `<note ${k}>${h}</note>` };
const fmtWhenShort = () => "", updateStatusHtml = () => "";
const SIGNATURE_TEXT = { signed: "חתום", unsigned: "לא חתום", unchecked: "לא נבדק" };
""" + _js("signatureText", js) + "\n" + _js("updateActionsHtml", js) + """
let updateError = "", UPDATE_STATUS = null;
let HEALTH_UPDATE = { enabled: true };
let UPDATE_CHECK = { available: true, latest: "v0.25.0", signature: { state: "signed", detail: "" },
  skipped: [{ tag: "v9.9.9", state: "unsigned", detail: "d" }] };
const out = { skipped: updateActionsHtml() };
UPDATE_CHECK = { available: true, latest: "v0.25.0", signature: { state: "signed", detail: "" } };
out.plain = updateActionsHtml();
console.log(JSON.stringify(out));
""", encoding="utf-8", newline="\n")
    got = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert "<note warn>" in out["skipped"] and "v9.9.9" in out["skipped"], out["skipped"]
    assert "לא חתום" in out["skipped"]
    assert "החל עדכון v0.25.0" in out["skipped"] and "disabled" not in out["skipped"]
    assert "<note warn>" not in out["plain"] and "v9.9.9" not in out["plain"]
