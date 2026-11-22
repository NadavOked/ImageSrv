"""#3788: a write-check reason longer than the GUI's buffer ends in "…", and a reason that fits is drawn whole.

`IdleDisk.wtext` (field 19 of `cdisk=`) held 160 bytes and cut a longer reason without a mark, so the operator read a
sentence that stopped as if it were whole (Hebrew is 2 bytes a letter: about 80 letters). It holds 320 now, and a cut
is said with "…" on a character boundary.

The binary logs each string it draws (``IMAGESRV_GUI_BOXLOG``); the slow-disk line is ``<wtext> — לא כותבים על הדיסק הזה``.
Runs on the Testrunner only (cairo/pango, ``--png``), like the rest of the waiting-screen tests.
"""

from __future__ import annotations

from test_cloner_gui import _build_gui
from test_native_local_clone import pytestmark as _native
from test_room_precheck_native_432_65 import _GUI
from test_standby_reason_overlap_3738 import STATE, _boxes

pytestmark = _native

SUFFIX = " — לא כותבים על הדיסק הזה"
WORD = "מילה"                                    # 8 bytes
# 269 bytes: over the old 159 and under the new 319 -- it used to be cut, and it fits now
FITS = " ".join([WORD] * 30)
# 629 bytes: over any buffer, and every cut of it lands inside a two-byte letter at least once
TOO_LONG = " ".join([WORD] * 70)


def _drawn(tmp_path, reason: str) -> str:
    binary = _build_gui(tmp_path)
    cards = _boxes(binary, tmp_path, STATE.format(words="", wstate="slow", wtext=reason), "1280x800", "w")
    standby = cards["standby-light"]
    hits = [b[4] for b in standby if b[4].startswith(reason[:20])]
    assert len(hits) == 1, (len(hits), [b[4][:30] for b in standby])
    return hits[0]


@_GUI
def test_a_reason_longer_than_the_old_buffer_is_drawn_whole(tmp_path):
    assert len(FITS.encode()) > 159 and len(FITS.encode()) < 319
    assert _drawn(tmp_path, FITS) == FITS + SUFFIX


@_GUI
def test_a_reason_longer_than_the_buffer_ends_in_an_ellipsis_on_a_letter_boundary(tmp_path):
    drawn = _drawn(tmp_path, TOO_LONG)
    assert drawn.endswith("…" + SUFFIX), drawn[-60:]
    kept = drawn[:-len(SUFFIX)][:-1]                       # the reason as kept, without the mark
    assert TOO_LONG.startswith(kept) and len(kept) > 0
    assert 300 <= len(kept.encode()) <= 316, len(kept.encode())   # the whole buffer is used (319 bytes with the mark), not cut early
