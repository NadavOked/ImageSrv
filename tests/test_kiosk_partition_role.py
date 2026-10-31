"""#1591 -- the same chain as #1588, the dangerous direction.

``lc_role`` (lcinv.sh) falls back to ``_partition_role`` when the inventory has
no role for a partition; that function lives in manifest.sh, which the kiosk's
load chain (``gui_libs``) did not carry. The role came out EMPTY, and
``lc_min_free`` only refused ``unchecked``: an empty role went into the synthetic
manifest as ``"role":""``, ``system_free_after`` found no windows/linux partition
("none"), and ``min_free_check`` answered ``ok`` -- for a target the image does
not fit (C: 400 GB, 390 used, disk 410 GB, rule 20 GB).

Two fixes, each with its own test: the kiosk loads manifest.sh; and a role that
is not one of the known ones is "not checked" whatever produced it (principle 5).
The kiosk chain here is the real one (``gui_libs``).
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import AGENT, BASH, posix, sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH), "jq")

G = 1_000_000_000
MS_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"


def _lc_min_free(tmp_path: Path, roles: str | None, after_libs: str = "") -> tuple[str, str]:
    """lc_min_free as the kiosk calls it: C: 400 GB (390 used), a 410 GB target, rule 20 GB.
    ``roles`` is the inventory's roles file (None = missing); ``after_libs`` runs after gui_libs."""
    run = tmp_path / "run"; inv = run / "lc" / "inv"; inv.mkdir(parents=True)
    (run / "min_free_gb").write_text("20\n", newline="\n")
    (inv / "sda.parts").write_text(f"1|{MS_DATA}||2048|{400 * G // 512}\n", newline="\n")   # idx|type|uniq|first|sectors
    (inv / "sda.used").write_text(f"1|{390 * G}\n", newline="\n")
    if roles is not None:
        (inv / "sda.roles").write_text(roles, newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(tmp_path / "gui")!r} IMAGESRV_TEST=1 '
             f'LIB_DIR={posix(flat_lib())!r}; . {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; '
             f'{after_libs} lc_min_free sda {410 * G} {posix(inv)}/sda.parts')
    assert out.returncode == 0, out.stderr
    return out.stdout.strip(), out.stderr


def test_the_kiosk_chain_knows_where_partition_role_is(tmp_path):
    out = sh(f'export RUN_DIR={posix(tmp_path)!r} LIB_DIR={posix(flat_lib())!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; type _partition_role')
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_target_that_does_not_fit_is_short_when_the_inventory_has_no_role_line(tmp_path):
    verdict, err = _lc_min_free(tmp_path, roles=None)       # the fallback to the GUID's role
    assert "not found" not in err, err
    assert verdict.startswith("short|"), verdict


def test_the_same_target_is_short_with_the_inventorys_own_role(tmp_path):
    # a guard that holds in both states: the normal path never reaches the fallback
    verdict, _ = _lc_min_free(tmp_path, roles="1|windows\n")
    assert verdict.startswith("short|"), verdict


def test_an_empty_role_is_unchecked_never_ok(tmp_path):
    # whatever produced it -- here a resolver that answers nothing
    verdict, _ = _lc_min_free(tmp_path, roles=None, after_libs="_partition_role() { :; };")
    assert verdict.startswith("unchecked|"), verdict


def test_a_role_nobody_knows_is_unchecked_never_ok(tmp_path):
    verdict, _ = _lc_min_free(tmp_path, roles="1|bogus\n")
    assert verdict.startswith("unchecked|"), verdict
