"""#1447: on metal 16/10 the cloner GUI clock was UTC, three hours behind.

The tested chain is server hello/state ``tz`` -> guistate.sh ``tz=`` -> the
GUI setting process ``TZ``. Negative control: without these production links,
the API/record/source assertions fail instead of accepting the UTC clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.system import timezone
from server.db import connect, set_setting
from test_class_deploy_switch import STATION, state_records
from test_cloner_gui import BASH, posix, sh
from conftest import hello_body

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
GUI = REPO / "native-gui" / "src"

try:
    import tzdata
except ImportError:
    tzdata = None

SYSTEM_ZONEINFO = Path("/usr/share/zoneinfo")
REAL_ZONEINFO = (Path(tzdata.__file__).parent / "zoneinfo"
                 if tzdata is not None else SYSTEM_ZONEINFO)
real_zoneinfo = requires_native(
    paths=(str(REAL_ZONEINFO / "Asia" / "Jerusalem"),),
    why="a real TZif database is required")


def _db(tmp_path: Path, zone: str | None = None):
    conn = connect(tmp_path / "test.db")
    if zone is not None:
        set_setting(conn, "timezone", zone)
    return conn


def _tzif(footer: bytes = b"IST-2IDT,M3.4.4/26,M10.5.0") -> bytes:
    return b"TZif2" + b"\0" * 40 + b"\n" + footer + b"\n"


def _zone(root: Path, name: str = "Asia/Jerusalem", data: bytes | None = None) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_tzif() if data is None else data)


@real_zoneinfo
def test_default_zone_uses_the_real_jerusalem_tzif(tmp_path, monkeypatch):
    monkeypatch.setattr(timezone, "ZONEINFO", REAL_ZONEINFO)
    assert timezone.posix_tz(_db(tmp_path)) == "IST-2IDT,M3.4.4/26,M10.5.0"


def test_fake_tzif_footer_is_returned(tmp_path, monkeypatch):
    root = tmp_path / "zones"; _zone(root, "Test/Zone", _tzif(b"XYZ-5"))
    monkeypatch.setattr(timezone, "ZONEINFO", root)
    assert timezone.posix_tz(_db(tmp_path, "Test/Zone")) == "XYZ-5"


@pytest.mark.parametrize("zone", ["../../etc/passwd", "/etc/x", "Asia\\Jerusalem"])
def test_unsafe_zone_names_are_rejected(tmp_path, monkeypatch, zone):
    monkeypatch.setattr(timezone, "ZONEINFO", tmp_path / "zones")
    assert timezone.posix_tz(_db(tmp_path, zone)) is None


@pytest.mark.parametrize("data", [
    b"not a TZif\nXYZ-5\n",
    b"TZif1" + b"\0" * 40 + b"\nXYZ-5\n",
    b"TZif2" + b"\0" * 40 + b"\nXYZ-5",
    b"TZif2" + b"\0" * 40 + b"\n\n",
    _tzif(b"XYZ 5"),
    _tzif(b"XYZ;5"),
    _tzif(b"X" * 65),
])
def test_malformed_tzif_returns_none(tmp_path, monkeypatch, data):
    root = tmp_path / "zones"; _zone(root, data=data)
    monkeypatch.setattr(timezone, "ZONEINFO", root)
    assert timezone.posix_tz(_db(tmp_path)) is None


def test_missing_zoneinfo_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(timezone, "ZONEINFO", tmp_path / "missing")
    assert timezone.posix_tz(_db(tmp_path)) is None


def test_hello_unknown_known_cloner_and_state_carry_tz(server, tmp_path, monkeypatch):
    root = tmp_path / "zones"; _zone(root)
    monkeypatch.setattr(timezone, "ZONEINFO", root)
    expected = "IST-2IDT,M3.4.4/26,M10.5.0"

    unknown = server["anon"].post("/api/v1/agent/hello", json=hello_body(
        "b4:2e:99:07:1a:fe")).json()
    assert unknown["known"] is False and unknown["tz"] == expected

    mac = "b4:2e:99:07:1a:c1"
    added = server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "Cloner1", "group_id": "grp_CLONERS"})
    assert added.status_code == 200, added.text
    known = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
    assert known["known"] is True and known["tz"] == expected
    state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    assert state["tz"] == expected


def test_api_keeps_tz_key_when_zoneinfo_is_missing(server, tmp_path, monkeypatch):
    monkeypatch.setattr(timezone, "ZONEINFO", tmp_path / "missing")
    mac = "b4:2e:99:07:1a:fd"
    hello = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
    state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    assert "tz" in hello and hello["tz"] is None
    assert "tz" in state and state["tz"] is None


@requires_native(("bash", BASH), "jq", why="guistate.sh emits records with jq")
def test_gui_state_emits_tz_and_empty_for_null_or_missing(tmp_path):
    value = "IST-2IDT,M3.4.4/26,M10.5.0"
    rows = state_records(tmp_path / "value", None, station={**STATION, "tz": value})
    null = state_records(tmp_path / "null", None, station={**STATION, "tz": None})
    missing = state_records(tmp_path / "missing", None, station=STATION)
    assert f"tz={value}" in rows
    assert "tz=" in null and "tz=" in missing


@requires_native(("bash", BASH), "jq", why="gui_header_state reads JSON with jq")
def test_cloner_cached_hello_emits_tz(tmp_path):
    response = tmp_path / "response.json"
    response.write_text('{"tz":"IST-2IDT,M3.4.4/26,M10.5.0"}', newline="\n")
    script = f'. {posix(AGENT)}/lib/gui/guistate.sh; gui_header_state {posix(response)!r}'
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "tz=IST-2IDT,M3.4.4/26,M10.5.0" in out.stdout.splitlines()


def test_gui_source_applies_tz_and_labels_unknown_clock_utc():
    state = (GUI / "state.c").read_text(encoding="utf-8")
    screens = (GUI / "screens.c").read_text(encoding="utf-8")
    assert 'KEY("tz")' in state and 'setenv("TZ"' in state
    assert '_putenv_s("TZ"' in state and "tzset();" in state and '"UTC0"' in state
    assert "apply_tz(s->tz);" in state
    assert '"%H:%M UTC"' in screens and '"12:45"' in screens


def test_installer_sets_the_chosen_zone_before_timesyncd():
    # 17/10: the zone is the installer's question (default Asia/Jerusalem,
    # tests/test_installer_timezone_1447.py), no longer fixed here.
    finish = (REPO / "installer" / "lib" / "finish.sh").read_text(encoding="utf-8")
    link = 'chroot "$TARGET" ln -sf "/usr/share/zoneinfo/$ANSWER_TIMEZONE" /etc/localtime'
    assert link in finish
    assert 'write_target 0644 /etc/timezone "$ANSWER_TIMEZONE' in finish
    assert finish.index(link) < finish.index("systemctl enable chrony.service")
