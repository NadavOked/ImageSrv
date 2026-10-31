"""#1412 -- the rest of "done": any overlay, and a site hook, keep a lab initrd out of a product ISO.

``test_iso_lab_gate_1412.py`` pins dropbear and ``etc/imagesrv/authorized_keys``. The
Issue's own definition of done asks for more, and the lab overlay as it exists
(``C:\\ImageCtl-Lab\\tech-overlay``) is exactly that more: a hook in
``etc/imagesrv/hooks.d/`` that starts dropbear. A variant of it -- a hook that starts
busybox telnetd, a key in ``root/.ssh`` -- passed the gate before this:

* ``build_initramfs.sh`` writes ``etc/imagesrv/overlay.txt`` whenever
  ``IMAGESRV_EXTRA_OVERLAY`` is applied, and the gate refuses that marker;
* a file under ``etc/imagesrv/hooks.d/`` is refused (the product ships none);
* ``authorized_keys`` anywhere is refused, not only under ``etc/imagesrv``;
* a ``grep`` that broke (rc 2) stops the build -- it was read as "no lab material" (#1630).

Negative control: the gate before this change (``origin/lab/int-1022``) passes the
first three unmarked and turns the broken grep into ``false``.
"""

from __future__ import annotations

import subprocess

import pytest

from native import requires_native
from test_iso_lab_gate_1412 import LAB_GATE, REPO, gate, initrd

OVERLAY_ONLY = {
    "site_hook": ("etc/imagesrv/hooks.d/50-x.sh",),
    "overlay_marker": ("etc/imagesrv/overlay.txt",),
    "root_ssh_key": ("root/.ssh/authorized_keys",),
}


@requires_native("bash", "cpio", "gzip", "zstd")
@pytest.mark.parametrize("comp", ["gzip", "zstd"])
@pytest.mark.parametrize("what", sorted(OVERLAY_ONLY))
def test_an_overlay_without_dropbear_is_still_refused_without_the_flag(tmp_path, what, comp):
    img = initrd(tmp_path, OVERLAY_ONLY[what], comp)
    refused = gate(img)
    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    assert "--lab" in refused.stderr, refused.stderr
    allowed = gate(img, "--lab")
    assert allowed.returncode == 0 and allowed.stdout.strip() == "true", (allowed.stdout, allowed.stderr)


@requires_native("bash", "cpio", "gzip", "zstd")
def test_a_grep_that_broke_stops_the_gate_instead_of_reading_clean(tmp_path):
    """grep rc 2 is "the check broke" -- never "no lab material" (principle 5)."""
    img = initrd(tmp_path, ("usr/sbin/dropbear",), "gzip")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "grep").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8", newline="\n")
    (stubs / "grep").chmod(0o755)
    run = subprocess.run(["bash", str(LAB_GATE), str(img)], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL, env={"PATH": f"{stubs}:/usr/bin:/bin"})
    assert run.returncode != 0, (run.stdout, run.stderr)
    assert run.stdout.strip() != "false", run.stdout
    assert "grep rc=2" in run.stderr, run.stderr


def test_the_marker_build_initramfs_writes_is_the_one_the_gate_refuses():
    """Static drift guard: the two files name the same path (the build itself needs root and apt)."""
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    block = build.split('if [ -n "${EXTRA_OVERLAY:-}" ]; then', 1)[1].split("\nfi\n", 1)[0]
    assert '"$ROOT/etc/imagesrv/overlay.txt"' in block, block
    assert r"etc/imagesrv/overlay\.txt$" in LAB_GATE.read_text(encoding="utf-8")


@requires_native("bash", "cpio", "gzip")
def test_a_clean_initrd_with_no_lab_material_still_passes(tmp_path):
    """Guard against an over-broad gate: modules, init and agent files are not lab access."""
    img = initrd(tmp_path, ("etc/imagesrv/version", "usr/lib/imagesrv/agent/lib/sitehooks.sh"), "gzip")
    clean = gate(img)
    assert clean.returncode == 0 and clean.stdout.strip() == "false", (clean.stdout, clean.stderr)
