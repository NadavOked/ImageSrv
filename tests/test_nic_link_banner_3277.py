"""#3277 (Nadav 17/11: "if the cloner is on 100 it has to tell me, like the message when it is on IDE") -- the link banner.

The distribution card's link is LOW when it is 100 Mb/s or less (a bottleneck on every network) or below the card's own
maximum. The cloner says so on its wait screen -- the same orange banner as the IDE one of the local clone screen
(warning_soft, a warn line, the led, ink text). The numbers in its text are the card's OWN reading
(``nic_link=<iface>|<Mb/s>|<max>|<flag>``, `cloner_gui_state`); WHETHER it is drawn is the brain's verdict, the flag (#3663, G2:
`link_verdict.banner`, tests/test_link_verdict_3663.py) -- the screen holds no threshold. The server journals the change once
(`nic_link_low` / `nic_link_back`); the console card shows the orange note it already had (#2847), now from the live speed.
Information only: nothing is asked and nothing is blocked.

Four layers: the agent's reading (sysinfo.sh), the GUI's pixels (C), the server's journal line, the Hebrew label.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.rounds import nic_live  # noqa: E402
from test_agent import AGENT, posix, sh  # noqa: E402
from test_native_standby_1304 import HEAD, TWO, _GUI, _count, _render, _token  # noqa: E402
from test_server_room import CLONER1, room_server  # noqa: E402,F401 -- fixture
from test_nic_live_3277 import BOOT_A, _hello  # noqa: E402

MAC = "aa:bb:cc:00:00:42"

ETHTOOL_1G = """#!/bin/sh
cat <<'EOT'
Settings for eth0:
	Supported ports: [ TP MII ]
	Supported link modes:   10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	                        1000baseT/Full
	Supported pause frame use: Symmetric
	Supports auto-negotiation: Yes
	Advertised link modes:  10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	                        1000baseT/Full
	Speed: 100Mb/s
EOT
"""

ETHTOOL_10G = ETHTOOL_1G.replace("1000baseT/Full\n\tSupported pause", "1000baseT/Full\n\t                        10000baseT/Full\n\tSupported pause")


# --- the server --------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _forget():
    nic_live.forget()
    yield
    nic_live.forget()


@pytest.mark.parametrize("mbps, top, low", [
    (100, 1000, True), (100, None, True), (10, None, True),      # 100 or less: a bottleneck on every network
    (1000, 2500, True), (2500, 10000, True),                     # below the card's own maximum
    (1000, 1000, False), (1000, None, False),                    # a full link, or a card that does not say its maximum
    (2500, 1000, False),
    (None, 1000, False),                                         # not read: not low -- and not "fine" either (the caller)
])
def test_the_link_is_low_at_100_or_less_or_below_the_cards_maximum(mbps, top, low):
    assert nic_live.is_low(mbps, top) is low


def test_a_change_is_returned_once_and_a_steady_state_says_nothing():
    note = lambda mbps, at: nic_live.note(MAC, {"iface": "eth0", "speed_mbps": mbps, "max_mbps": 1000}, BOOT_A, now=at)  # noqa: E731
    assert note(1000, 1) is None                 # first sight, full: nothing to say
    assert note(1000, 2) is None
    assert note(100, 3) == ("low", 100, 1000)    # it dropped
    assert note(100, 4) is None                  # ...once
    assert note(None, 5) is None                 # unreadable now: the previous state is kept, no "back" on a guess
    assert note(100, 6) is None
    assert note(1000, 7) == ("back", 1000, 1000)
    assert note(100, 8) == ("low", 100, 1000)


def test_a_machine_first_seen_already_low_is_journaled_as_low():
    assert nic_live.note(MAC, {"speed_mbps": 100, "max_mbps": 1000}, None, now=1) == ("low", 100, 1000)


def test_the_hello_journals_the_drop_and_the_return_once_each(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    rows = lambda ev: [r["detail"] for r in ctx.conn.execute("SELECT detail FROM journal WHERE event = ?", (ev,))]  # noqa: E731
    _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000, "max_mbps": 1000}, boot_id=BOOT_A)
    assert rows("nic_link_low") == [] and rows("nic_link_back") == []
    for _ in range(3):
        _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 100, "max_mbps": 1000}, boot_id=BOOT_A)
    (low,) = rows("nic_link_low")
    assert CLONER1 in low and "100Mb/s" in low and "max 1000Mb/s" in low, low
    _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000, "max_mbps": 1000}, boot_id=BOOT_A)
    assert len(rows("nic_link_back")) == 1 and len(rows("nic_link_low")) == 1


def test_the_two_events_have_a_hebrew_label_and_a_severity():
    from server.console import journal_he
    assert "בקישור נמוך" in journal_he.EVENTS_HE["nic_link_low"] and "חזר לקישור מלא" in journal_he.EVENTS_HE["nic_link_back"]
    assert "nic_link_low" in journal_he._SEVERITY_WARN and "nic_link_back" in journal_he._SEVERITY_OK


# --- the agent -----------------------------------------------------------------------------------------------

def _agent(tmp_path: Path, script: str, ethtool: str | None, speed: str | None = "100\n") -> str:
    root = tmp_path / "root"
    (root / "sys/class/net/eth0").mkdir(parents=True, exist_ok=True)
    if speed is not None:
        (root / "sys/class/net/eth0/speed").write_text(speed, newline="\n")
    run = tmp_path / "run"; run.mkdir(parents=True, exist_ok=True)
    fake = tmp_path / "ethtool"
    if ethtool is not None:
        fake.write_text(ethtool, newline="\n"); fake.chmod(0o755)
    et = posix(fake) if ethtool is not None else "/nonexistent/ethtool"
    return sh(f'export SYSROOT={posix(root)!r} RUN_DIR={posix(run)!r} IFACE=eth0 ETHTOOL={et!r}; '
              f'. {posix(AGENT)}/lib/hw/sysinfo.sh; {script}')


def test_the_agent_reads_the_cards_maximum_from_ethtool_and_sends_it(tmp_path):
    out = _agent(tmp_path, "nic_link_json", ETHTOOL_1G)
    assert out == (',"nic_link":{"iface":"eth0","speed_mbps":100,"max_mbps":1000,'
                   '"carrier_changes":null,"carrier_first":null,"cable_tested":false}'), out   # #3663: facts for the brain, null = not read


def test_a_ten_gig_card_says_ten_thousand(tmp_path):
    assert '"max_mbps":10000' in _agent(tmp_path, "nic_link_json", ETHTOOL_10G)


def test_without_ethtool_the_maximum_is_null_and_the_speed_is_still_said(tmp_path):
    out = _agent(tmp_path, "nic_link_json", None)
    assert out == (',"nic_link":{"iface":"eth0","speed_mbps":100,"max_mbps":null,'
                   '"carrier_changes":null,"carrier_first":null,"cable_tested":false}'), out


def test_the_maximum_is_read_once_per_boot(tmp_path):
    assert _agent(tmp_path, "nic_max_mbps", ETHTOOL_1G) == "1000"
    assert (tmp_path / "run" / "nic.max").read_text() == "1000"
    gone = "#!/bin/sh\nexit 9\n"                       # ethtool fails now: the cache answers, it is not run on every hello
    assert _agent(tmp_path, "nic_max_mbps", gone) == "1000"


def test_the_screen_record_is_iface_speed_max_and_empty_when_unread(tmp_path):
    assert _agent(tmp_path, "nic_link_record", ETHTOOL_1G) == "nic_link=eth0|100|1000|\n"   # #3663: no verdict from the server = an empty flag
    assert _agent(tmp_path / "b", "nic_link_record", None, speed="-1\n") == "nic_link=eth0|||\n"   # a card with no ethtool and no link


def test_cloner_gui_state_carries_the_record():
    text = (AGENT / "lib/gui/clonergui.sh").read_text(encoding="utf-8")
    assert 'cloner_nic_link >> "$_cg_next" || return 1' in text   # #3644: the record's failure is a log line, never `|| :` (tests/test_nic_link_kiosk_3644.py runs it)


# --- the GUI: pixels, like the IDE banner --------------------------------------------------------------------

BODY = "hello_age=4\nhello_rc=200\n" + TWO


def _soft(img, theme):
    return _count(img[theme], (0, 0, 1280, 800), _token("--clr-warning-soft", theme), tol=2)


@_GUI
@pytest.mark.parametrize("line, banner", [
    ("nic_link=eth0|100|1000|1\n", True),      # the case: 100 on a gigabit card, and the brain said SHOW
    ("nic_link=eth0|100||1\n", True),          # 100 on a card that does not say its maximum
    ("nic_link=eth0|1000|2500|1\n", True),     # below the card's maximum
    ("nic_link=eth0|1000|1000|1\n", True),     # #3663: the flag is the whole decision -- the screen does not look at the numbers
    ("nic_link=eth0|100|1000|0\n", False),     # the brain said no (NONE / NOT_MEASURED), whatever the numbers
    ("nic_link=eth0|100|1000|\n", False),      # #3663: no verdict (an old server, a rule that failed) = no banner, even at 100: the GUI holds no threshold
    ("nic_link=eth0|100|1000\n", False),       # an old agent: three fields, no verdict
    ("nic_link=eth0|1000|1000|0\n", False),
    ("nic_link=eth0|||1\n", False),            # a verdict on a link that was not read: no banner on a half-read link
    ("", False),                               # an old agent
])
def test_the_banner_is_the_ide_banner_in_pixels(tmp_path, line, banner):
    plain = _render(tmp_path, "plain", BODY)
    got = _render(tmp_path, "got", BODY + line)
    for theme in ("light", "dark"):
        extra = _soft(got, theme) - _soft(plain, theme)
        if banner:
            assert extra > 5000, (theme, extra)                       # the box: ~980 x 40 of warning-soft
            w = _count(got[theme], (0, 0, 1280, 800), _token("--clr-warning", theme), tol=2) - \
                _count(plain[theme], (0, 0, 1280, 800), _token("--clr-warning", theme), tol=2)
            assert w > 100, (theme, w)                                # and its warn line
        else:
            assert abs(extra) < 50, (theme, extra)
