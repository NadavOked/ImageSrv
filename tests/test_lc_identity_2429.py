"""#2429 -- a local clone compares an identity, never "no serial" with "no serial".

``lc_run`` checked each disk against the inventory the person saw once, as
``serial now == serial then`` -- and a disk with no serial passed: "" = "".
After that check every write went to the name (``apply_gpt``, each ``partclone
-O``, ``finish_grow``/dd), so a drawer swapped during the minutes of the shrink,
or between partitions, was written.

Now an empty serial is no identity (as lcsize.sh, smart.sh and failmark.sh already
said), a target is compared by what target_init bound, and the copy's writes ask
``assert_target`` again (test_target_identity_2434.py has the write sites).

The harness is test_no_wipe_before_write_1409's: the real ``lc_run`` up to
``lc_source_parts``, where the run stops. ``$EMPTY`` lists the disks that answer
no serial -- in sysfs now and in the inventory line alike.

Negative control: against a000bec7 both disks pass the identity check and the
run reaches lc_source_parts ("stopped-here") -- the commit message has the output.
"""

from __future__ import annotations

import test_no_wipe_before_write_1409 as base
from native import requires_native
from test_local_clone_agent import BASH
from test_no_wipe_before_write_1409 import ZAP_HE

pytestmark = requires_native(("bash", BASH))

EMPTY_SERIAL = (
    'disk_serial() { case " ${EMPTY:-} " in *" $1 "*) ;; *) printf "S-%s" "$1" ;; esac; }; '
    'lc_inv_field() { case "$2" in 4) case " ${EMPTY:-} " in *" $1 "*) ;; *) printf "S-%s" "$1" ;; esac ;; '
    '12) echo "${SRC_OK:-1}" ;; *) echo ;; esac; }; '
)


def lc_empty(tmp_path, targets: str, **env):
    """base.lc with EMPTY_SERIAL after its harness (lc reads the module's HARNESS)."""
    saved = base.HARNESS
    base.HARNESS = saved + EMPTY_SERIAL
    try:
        return base.lc(tmp_path, targets, **env)
    finally:
        base.HARNESS = saved


def test_a_target_with_no_serial_is_not_the_same_as_an_inventory_with_none(tmp_path):
    states, calls, out = lc_empty(tmp_path, "sdb", EMPTY="sdb")
    assert states["sdb"] == f"failed|{ZAP_HE}", (states, out.stderr)
    assert calls == [], calls


def test_a_source_with_no_serial_is_not_the_disk_that_was_checked(tmp_path):
    states, _calls, out = lc_empty(tmp_path, "sda", EMPTY="sdc")
    assert states["sda"].startswith("failed|דיסק המקור אינו מה שנבדק"), (states, out.stderr)


def test_targets_with_serials_still_reach_the_copy(tmp_path):
    """Positive control: the same run with serials goes on to lc_source_parts."""
    states, _calls, out = lc_empty(tmp_path, "sda sdb")
    for d in ("sda", "sdb"):
        assert states[d] == "failed|stopped-here — לא נכתב דבר", (states, out.stderr)
