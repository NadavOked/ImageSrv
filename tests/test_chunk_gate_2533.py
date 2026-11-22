"""#2533, שלב 3: `chunk_gate` (`agent/lib/restore/chunkgate.sh`) — איזה מנוע מקבל את הגל.

מנוע החתיכות הוא המנוע היחיד (#3173), והשרת אומר אותו ב-`session.engine` של תשובת ה-hello.
כל מצב שאינו "chunk עם שלושת הפרמטרים שלו, על אימג' בפורמט 2, עם מקלט באימג'" -- כולל
hello בלי השדה, או עם המנוע הישן בשמו -- הוא סירוב בשם, לפני שנוגעים בדיסק: אין נתיב אחר ליפול אליו.

**בקרה שלילית (#3173):** עם `agent/lib/restore/chunkgate.sh` של fa33a4068 שלוש הבדיקות הראשונות נופלות:
hello בלי מנוע או עם `udpcast` עובר (`rc=0 mode=multicast`), ומנוע לא מוכר נדחה בנוסח האחר.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
JQ_SHIM = posix(REPO / "tests" / "jq_shim.py")
JQ_FN = "" if shutil.which("jq") else f'jq() {{ python "{JQ_SHIM}" "$@"; }}; '
pytestmark = requires_native(("bash", BASH))

CHUNK = {"group": "239.84.82.1", "port": 7713, "key": "0123456789abcdef"}   # פורט-הפצה-מכוון


def _gate(tmp_path, session: dict, *, image_format=2, receiver=True) -> str:
    resp = {"session": {"id": "ses_1", "image_id": "img_1", "state": "running", **session}}
    (tmp_path / "resp.json").write_text(json.dumps(resp), encoding="utf-8")
    manifest = {"id": "img_1", **({"image_format": image_format} if image_format is not None else {})}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # the receiver "in the image" is a shell function here: `command -v` finds it on every platform
    have = "chunkcast() { :; }; " if receiver else ""
    script = (
        'export PATH="/usr/bin:$PATH"; '
        f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path / 'agent.log')!r}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f". {posix(AGENT)}/lib/restore/chunkgate.sh; " + JQ_FN + have
        + f'chunk_gate {posix(tmp_path / "resp.json")!r} {posix(tmp_path / "manifest.json")!r}; '
        'echo "rc=$? mode=$STREAM_MODE to=$CHUNK_GROUP:$CHUNK_PORT key=$CHUNK_KEY ses=$CHUNK_SESSION why=$CHUNK_REFUSAL src=$CHUNK_SOURCE"'
    )
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL)
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.startswith("rc=")]
    return lines[-1] if lines else proc.stdout + proc.stderr


def test_no_engine_field_is_refused_by_name(tmp_path):
    """#3173: hello בלי `session.engine` אינו "המנוע הישן" אלא שרת שלא הציע את מנוע החתיכות."""
    out = _gate(tmp_path, {}, image_format=None, receiver=False)
    assert out.startswith("rc=1 mode= ") and "did not offer the chunk engine (session.engine 'null')" in out, out
    assert "nothing was written" in out


def test_the_old_engine_by_name_is_refused_by_name(tmp_path):
    out = _gate(tmp_path, {"engine": "udpcast", "chunk": CHUNK})
    assert out.startswith("rc=1 mode= ") and "did not offer the chunk engine (session.engine 'udpcast')" in out, out


def test_the_chunk_engine_with_its_three_parameters_is_taken(tmp_path):
    out = _gate(tmp_path, {"engine": "chunk", "chunk": CHUNK})
    assert out.startswith("rc=0 mode=chunk to=239.84.82.1:7713 key=0123456789abcdef ses=ses_1 why="), out   # פורט-הפצה-מכוון


def test_an_engine_this_agent_does_not_know_is_refused_by_name(tmp_path):
    out = _gate(tmp_path, {"engine": "warp", "chunk": CHUNK})
    assert out.startswith("rc=1 mode= ") and "(session.engine 'warp')" in out and "nothing was written" in out


@pytest.mark.parametrize("field,bad", [
    ("port", 80), ("port", "9o02"), ("port", 70000), ("port", None),
    ("group", "239.83.67"), ("group", "group.example"), ("group", None),
    ("key", "0123456789ABCDEF"), ("key", "0123"), ("key", None),
])
def test_a_chunk_session_with_an_unusable_parameter_is_refused_not_guessed(tmp_path, field, bad):
    chunk = {**CHUNK, field: bad}
    if bad is None:
        del chunk[field]
    out = _gate(tmp_path, {"engine": "chunk", "chunk": chunk})
    assert out.startswith("rc=1 mode= ") and "is not usable -- nothing was written" in out, out


def test_an_old_format_image_is_refused_for_the_chunk_engine(tmp_path):
    out = _gate(tmp_path, {"engine": "chunk", "chunk": CHUNK}, image_format=None)
    assert out.startswith("rc=1 mode= ") and "needs a format-2 image" in out, out


def test_an_initramfs_without_the_receiver_refuses_by_name(tmp_path):
    out = _gate(tmp_path, {"engine": "chunk", "chunk": CHUNK}, receiver=False)
    assert out.startswith("rc=1 mode= ") and "has no chunkcast" in out, out


# --- #1079 / #2485: the address the receiver takes packets from (`session.chunk.source`) -----------------------

def _log(tmp_path) -> str:
    return (tmp_path / "agent.log").read_text(encoding="utf-8") if (tmp_path / "agent.log").exists() else ""


def test_the_source_the_server_names_is_taken_and_said(tmp_path):
    out = _gate(tmp_path, {"engine": "chunk", "chunk": {**CHUNK, "source": "10.55.0.5"}})
    assert out.startswith("rc=0 mode=chunk ") and out.endswith(" src=10.55.0.5"), out
    assert "from 10.55.0.5 only" in _log(tmp_path) and "WARNING" not in _log(tmp_path), _log(tmp_path)


@pytest.mark.parametrize("chunk", [CHUNK, {**CHUNK, "source": None}, {**CHUNK, "source": ""}])
def test_a_hello_without_a_source_is_todays_behaviour_with_a_warning_in_the_log(tmp_path, chunk):
    """שרת ישן, או שרת שאין לו כתובת לומר: הסבב רץ כמו היום (כל מקור), ושורה ב-WARNING אומרת למה."""
    out = _gate(tmp_path, {"engine": "chunk", "chunk": chunk})
    assert out.startswith("rc=0 mode=chunk ") and out.endswith(" src="), out
    assert "WARNING" in _log(tmp_path) and "named no source address" in _log(tmp_path), _log(tmp_path)


@pytest.mark.parametrize("bad", ["10.55.0", "srv.example", "1.2.3.4.5", "10..0.1", "::1", 5, True])
def test_a_source_that_is_not_an_ipv4_address_is_refused_by_name_not_dropped_to_any_source(tmp_path, bad):
    out = _gate(tmp_path, {"engine": "chunk", "chunk": {**CHUNK, "source": bad}})
    assert out.startswith("rc=1 mode= ") and "source" in out and "is not usable -- nothing was written" in out, out
