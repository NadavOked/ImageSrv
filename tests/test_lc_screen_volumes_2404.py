"""#2404 -- local clone screen: "נכנס" on the compacted layout, and disk_fits refuses later.

After #1897 (Nadav, 04/11) every partition capture shrank goes back to its
source size, and only the one expand_last stretches (C:) gives way. The run's
manifest carries ``shrunk_from_bytes`` and ``disk_fits`` asks ``volumes_fit``
and ``system_free_after`` on it. The screen's verdict (``lc_verdict_for`` ->
``lc_min_free``, lcinv.sh) built its manifest WITHOUT ``shrunk_from_bytes``:
a target that holds the compacted C:+D: was drawn "shrink" (green), and the
run then refused it by name, before the first byte. Safe (principle 5), but
the operator saw green and then a refusal.

The screen now asks the same function the run asks (``fit_layout_check``,
diskfit.sh), on a manifest that says which partitions come back.

The layout: ESP, C: (windows, 400 GB at the source, 90 GB compacted) and D:
(data, 100 GB at the source, 20 GB compacted), a 520 GB source.
  * 195 GB target: C: gets 94.8 GB with D: back at 100 GB -- C: needs 90.
  * 185 GB target: C: gets 84.8 GB -- the compacted layout (110 GB) still fits
    the disk, D: does not fit at its source size next to a 90 GB C:.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from agentlib import flat_lib
from test_agent import BASH, posix, sh
from test_min_free_1354 import fits

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GB = 10**9
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"

C_START = 206848
C_SRC, C_SMALL = 400 * GB // 512, 90 * GB // 512
D_SRC, D_SMALL = 100 * GB // 512, 20 * GB // 512
D_START_SRC = C_START + C_SRC
D_START_SMALL = C_START + C_SMALL


def _parts(c_size: int, d_size: int, d_start: int) -> str:
    return (f"1|{ESP}|U1|2048|204800\n2|{WIN}|U2|{C_START}|{c_size}\n3|{WIN}|U3|{d_start}|{d_size}\n")


def _need(parts: str) -> int:
    end = max((int(p.split("|")[3]) + int(p.split("|")[4])) * 512 for p in parts.splitlines())
    return end + 262144


def screen(tmp_path: Path, target: int, rule: str, shrink: bool = True) -> str:
    """lc_verdict_for src tgt over files the inventory and the measurement left, with the real
    diskfit.sh/expand.sh and no stubbed jq."""
    run = tmp_path / "run"; lc = run / "lc"
    (lc / "inv").mkdir(parents=True); (lc / "measure").mkdir(parents=True)
    (run / "min_free_gb").write_text(rule + "\n")
    src = _parts(C_SRC, D_SRC, D_START_SRC)
    (lc / "inv" / "src.parts").write_text(src)
    (lc / "inv" / "src.roles").write_text("1|esp\n2|windows\n3|data\n")          # D: is data, not C:
    (lc / "inv" / "src.used").write_text(f"1|{30 << 20}\n2|{40 * GB}\n3|{10 * GB}\n")
    (lc / "inv" / "src.line").write_text(f"1|src|M|S1|{520 * GB}|ok||Windows|3|0|{_need(src)}|1|\n")
    (lc / "inv" / "tgt.line").write_text(f"2|tgt|M|S2|{target}|ok||||0|-1|0|\n")
    small = _parts(C_SMALL, D_SMALL, D_START_SMALL)
    (lc / "measure" / "src.compact").write_text(small)
    (lc / "measure" / "src").write_text(f"done|{_need(small)}|\n")
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log LC={posix(lc)!r}; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in
                       ("common", "jsonq", "sysinfo", "diskinfo", "progress", "failmark", "restore", "expand", "diskfit", "manifest", "lcinv"))
             + "lc_verdict_for src tgt")
    return out.strip().splitlines()[-1]


@needs_shell
def test_a_target_that_holds_the_compacted_layout_but_not_d_at_its_source_size_is_short(tmp_path):
    v = screen(tmp_path, 185 * GB, rule="0")
    verdict, _need_bytes, reason = v.split("|", 2)
    assert verdict == "short", v
    # volumes_fit's own words: which partition comes back, and what C: is left with
    assert re.fullmatch(r"הדיסק קטן מדי ב-\d+ MiB: מחיצה 3 חוזרת לגודלה במקור, 100 GB — "
                        r"ולמחיצה 2 נשארים 84\.8 GB, והיא צריכה 90 GB", reason), reason


@needs_shell
def test_a_target_that_holds_c_at_its_compacted_size_with_d_back_is_still_shrink(tmp_path):
    assert screen(tmp_path, 195 * GB, rule="0").startswith("shrink|")


@needs_shell
def test_the_free_space_rule_counts_d_back_at_its_source_size(tmp_path):
    """195 GB: C: gets 94.8 GB, 40 GB used -- 51.1 GiB free, not the 125 GiB the compacted manifest said."""
    v = screen(tmp_path, 195 * GB, rule="100")
    assert v.startswith("short|") and "יישארו רק 51.1 GB פנויים ב-C:, המינימום 100GB" in v, v


@needs_shell
def test_no_shrink_no_return_the_unshrunk_layout_is_as_before(tmp_path):
    """A target that fits the source layout as it is: nothing comes back, the verdict stays "fits"."""
    run = tmp_path / "run"
    v = screen(tmp_path, 600 * GB, rule="20")
    assert v.startswith("fits|"), v
    assert (run / "lc" / "fit.json").exists()
    assert "shrunk_from_bytes" not in (run / "lc" / "fit.json").read_text()


@needs_shell
def test_the_screen_says_what_disk_fits_says_on_the_run_manifest(tmp_path):
    """One rule: the manifest the run writes for this clone, on the same 185 GB disk."""
    image = {
        "schema": 1, "id": "i", "name": "n", "family": 256, "scheme": "gpt", "sector_size": 512,
        "disk_guid": None, "source_disk_bytes": 520 * GB, "min_target_bytes": None,
        "partitions": [
            {"index": 1, "type_guid": ESP, "role": "esp", "fs": "vfat", "start_sector": 2048,
             "size_bytes": 204800 * 512, "used_bytes": 30 << 20, "file": "local:p1", "sha256": None, "expandable": False},
            {"index": 2, "type_guid": WIN, "role": "windows", "fs": "ntfs", "start_sector": C_START,
             "size_bytes": C_SMALL * 512, "used_bytes": 40 * GB, "file": "local:p2", "sha256": None,
             "expandable": False, "shrunk_from_bytes": C_SRC * 512},
            {"index": 3, "type_guid": WIN, "role": "data", "fs": "ntfs", "start_sector": D_START_SMALL,
             "size_bytes": D_SMALL * 512, "used_bytes": 10 * GB, "file": "local:p3", "sha256": None,
             "expandable": False, "shrunk_from_bytes": D_SRC * 512},
        ],
    }
    _rc, err, _ = fits(tmp_path / "run_side", image, 185 * GB, gb="0")
    shown = screen(tmp_path / "screen_side", 185 * GB, rule="0").split("|", 2)[2]
    assert err and err == shown, (err, shown)
