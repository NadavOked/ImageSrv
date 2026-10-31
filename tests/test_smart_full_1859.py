"""#1859 -- the full SMART: every attribute, Nadav's new orange, and what the server learns.

Nadav, 20/10 00:20: "yes to all", "if the server is smart and learns". Until now the
agent read 5/197/198/199 and nothing else (agent/lib/smart/smart.sh). Now:

* every attribute the disk reports goes to the server (ids and numbers in the hello,
  with the vendor's names in the round's disk_event) -- agent/lib/smart/smartattrs.sh;
* ORANGE (asked; no answer = write) on a disk that is otherwise ok: 5/187/10/196 over 0,
  an SSD worn to 90% or out of spare (170/173/177/231/233, NVMe Percentage Used /
  Available Spare), and what the server learned since this serial's previous round
  (183/188 that rose, a critical attribute that rose -> "replace the disk", hot while
  writing) -- server/disks/smart_history.py;
* RED unchanged (FAILED, 197/198, read-only); with no history, "no comparison yet".

The disk outputs are fixtures_smart.py's (860 EVO, MZ7LN256, NVMe, no SMART) -- see
there why they are not lab captures.
"""

from __future__ import annotations

import json

import pytest

import fixtures_smart as F
from test_smart import BASH, PRELUDE, env, make_smartctl, preflight, sh

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")


def probe(tmp_path, body, dev="sda"):
    """smart_probe on a fake smartctl, ro=0; returns (verdict, cached record, attrs json)."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev_dir = tmp_path / "dev"; dev_dir.mkdir(exist_ok=True)
    (dev_dir / dev).write_bytes(b"\x00")
    blk = tmp_path / "sysro" / "sys" / "block" / dev; blk.mkdir(parents=True, exist_ok=True)
    (blk / "ro").write_text("0\n", newline="\n")
    sc = make_smartctl(tmp_path, body)
    out = sh(env(run, dev_dir, sysroot=tmp_path / "sysro", smartctl=sc) + PRELUDE
             + f'v=$(smart_probe {dev}); echo "$v"; smart_attrs_json {dev} 1')
    assert out.returncode == 0, out.stderr
    verdict, attrs = out.stdout.split("\n", 1)
    return verdict.strip(), (run / "smart" / f"{dev}.v").read_text().split(), json.loads(attrs)


# --- every attribute, read --------------------------------------------------------------


def test_an_860_evo_sends_every_attribute_it_reports(tmp_path):
    verdict, rec, attrs = probe(tmp_path, F.evo860())
    assert verdict == "ok" and rec[:2] == ["ok", "ok"]
    ids = [r[0] for r in attrs["ata"]]
    assert ids == [5, 9, 12, 177, 179, 181, 182, 183, 187, 190, 195, 199, 235, 241]
    wear = next(r for r in attrs["ata"] if r[0] == 177)
    assert wear == [177, 99, 99, 0, 5, "Wear_Leveling_Count"]
    assert next(r for r in attrs["ata"] if r[0] == 241)[4] == 28817154403
    assert attrs["nvme"] is None


def test_an_nvme_disk_sends_its_health_lines_by_name(tmp_path):
    verdict, _, attrs = probe(tmp_path, F.nvme(), dev="nvme0n1")
    assert verdict == "ok"
    assert attrs["ata"] == []
    assert attrs["nvme"] == {"critical_warning": 0, "temperature": 38, "available_spare": 100,
                             "available_spare_threshold": 10, "percentage_used": 3, "media_errors": 0,
                             "unsafe_shutdowns": 61, "power_on_hours": 5432, "power_cycles": 784,
                             "error_log_entries": 12}


def test_a_disk_without_smart_sends_null_not_an_empty_list(tmp_path):
    """"Not checked" is not "fine" (principle 5): no attributes is null."""
    verdict, rec, attrs = probe(tmp_path, F.NO_SMART)
    assert verdict == "unchecked" and rec[1] == "no_smart"
    assert attrs is None


# --- the static orange -------------------------------------------------------------------


@pytest.mark.parametrize("body,reason", [
    (F.evo860(realloc=3), "reallocated"),
    (F.evo860(uncorr=1), "reported_uncorrectable"),
    (F.evo860(wear_value=8, wear_raw=2950), "ssd_worn"),
    (F.mz7ln256(realloc=1), "reallocated"),
    (F.nvme(used=93), "ssd_worn"),
    (F.nvme(spare=5, spare_threshold=10), "spare_exhausted"),
])
def test_nadavs_orange_on_a_disk_that_is_otherwise_ok(tmp_path, body, reason):
    verdict, rec, _ = probe(tmp_path, body, dev="nvme0n1" if "NVMe" in body else "sda")
    assert (verdict, rec[1]) == ("warn", reason)


@pytest.mark.parametrize("body", [F.evo860(), F.mz7ln256(), F.nvme(), F.evo860(crc=55242), F.evo860(runtime_bad=4)])
def test_a_clean_disk_and_the_counters_that_do_not_colour_stay_ok(tmp_path, body):
    """199 is the cable (#872), and 183 colours only when it ROSE (the server learns it)."""
    verdict, _, _ = probe(tmp_path, body, dev="nvme0n1" if "NVMe" in body else "sda")
    assert verdict == "ok"


@pytest.mark.parametrize("body,reason", [
    (F.evo860(health="FAILED", realloc=3), "health_failed"),
    (F.nvme(critical="0x08"), "media_readonly"),   # #1921 ס1: NVMe read-only is red, and says so
])
def test_red_is_unchanged(tmp_path, body, reason):
    verdict, rec, _ = probe(tmp_path, body, dev="nvme0n1" if "NVMe" in body else "sda")
    assert (verdict, rec[1]) == ("fail", reason)


# --- the gate: orange -- and what the server learned -- is written without asking (#2042) ---


def test_a_static_orange_disk_is_written_without_a_question(tmp_path):
    written, run, err = preflight(tmp_path, F.evo860(realloc=3))
    assert written == "sda", err
    assert not (run / "asked").exists(), err   # #2042 (Nadav 23/10): until then asked, no answer = written
    assert (run / "targets/sda/smart_note").read_text(encoding="utf-8") == "SMART: סקטורים שהועברו (5)"


def learned_reply(reason: str) -> str:
    return ('json_get_join() { [ -s "$1" ] || return 1; echo ok; '
            'case "$2" in *smart_learned*) echo "S-sda|' + reason + '" ;; esac; }; '
            'disk_serial() { printf "S-%s" "$1"; }; ')


@pytest.mark.parametrize("reason", ["downshift_rose", "timeout_rose", "critical_rose", "hot"])
def test_what_the_server_learned_makes_a_clean_disk_orange(tmp_path, reason):
    written, run, err = preflight(tmp_path, F.evo860(), gui=["skip"], extra=learned_reply(reason))
    assert written == "sda", err                    # #2042: orange is written, the screen's "skip" never read
    assert not (run / "asked").exists(), err
    line = (run / "targets/sda/smart_note").read_text(encoding="utf-8")
    assert line.startswith("SMART: ") and reason not in line, line


def test_a_word_the_agent_does_not_know_is_not_an_orange(tmp_path):
    written, run, err = preflight(tmp_path, F.evo860(), extra=learned_reply("brand_new"))
    assert written == "sda" and not (run / "asked").exists(), err


def test_the_disk_event_carries_every_attribute_with_the_vendors_names(tmp_path):
    written, run, err = preflight(tmp_path, F.evo860(realloc=3), gui=["rescue"])
    ev = json.loads((run / "disk_event.json").read_text())
    assert ev["smart"]["verdict"] == "warn" and ev["smart"]["reason"] == "reallocated"
    assert [5, 100, 100, 10, 3, "Reallocated_Sector_Ct"] in ev["smart"]["attrs"]["ata"]
