"""#3776 (Nadav 19/11, docs/design/mockups-1119-pcibus): the PCI-bus banner of the cloner screens.

The brain decides (K-NICBUS: ``link_verdict.bus_banner``); the agent carries the verdict as ``nic_bus=<0|1>`` and the screen draws it,
holding no threshold of its own. The banner is the #3663 link banner (warning_soft, a warn line, the led). When the link is low AND the
bus is old it is ONE banner that names both, never two stacked. On the wait screen and, while cloning, between the metric cards and the
destinations table.
"""

from __future__ import annotations

import pytest

from test_link_verdict_3663 import _BASH, _facts
from test_native_standby_1304 import HEAD, TWO, _GUI, _count, _keep_fresh, _png, _png_rgb, _render, _token
from disk_rules_state import with_rules

WARN_SOFT, WARN = "--clr-warning-soft", "--clr-warning"


# --- the agent: the flag is the brain's, empty is no verdict is 0 ----------------------------------------------

@_BASH
@pytest.mark.parametrize("answer, flag", [
    ('{"link_verdict":{"bus_banner":"SHOW"}}', "1"), ('{"link_verdict":{"bus_banner":"NONE"}}', "0"),
    ('{"link_verdict":{"bus_banner":"NOT_MEASURED"}}', "0"), ('{"link_verdict":{"bus_banner":"show"}}', "0"),
    ('{"link_verdict":{"banner":"SHOW"}}', "0"),    # the LINK verdict is not the bus verdict
    ('{"schema":1}', "0"), ("{not json", "0"),
])
def test_the_bus_flag_is_one_only_on_the_brains_show(tmp_path, answer, flag):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "response.json").write_text(answer, newline="\n")
    assert _facts(tmp_path, "nic_bus_banner_record") == f"nic_bus={flag}\n"


@_BASH
def test_the_cloner_state_says_the_bus_flag_and_keeps_the_link_record_exact(tmp_path):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "response.json").write_text('{"link_verdict":{"banner":"SHOW","bus_banner":"SHOW"}}', newline="\n")
    out = _facts(tmp_path, "nic_link_record; nic_bus_banner_record", speed="100\n")
    assert out == "nic_link=eth0|100||1\nnic_bus=1\n", out


# --- the screen: pixels -----------------------------------------------------------------------------------------

BODY = "hello_age=4\nhello_rc=200\n" + TWO
CLONE = "cloner=Office 2024\ndrawer_count=2\ndrawer=1|sda|writing|45|100|\ndrawer=2|sdb|writing|20|100|\n"
BOX = (0, 0, 1280, 800)


def _extra(img, base, theme, token):
    return _count(img[theme], BOX, _token(token, theme), tol=2) - _count(base[theme], BOX, _token(token, theme), tol=2)


@_GUI
@pytest.mark.parametrize("line, banner", [
    ("nic_bus=1\n", True),                          # PCI alone: the banner (no nic_link needed)
    ("nic_bus=0\n", False),                         # the brain said no
    ("nic_bus=\n", False), ("nic_bus=2\n", False),   # anything but 1 draws nothing
    ("", False),                                    # an old agent: no verdict, no banner
])
def test_the_pci_banner_is_drawn_only_on_the_brains_flag(tmp_path, line, banner):
    plain = _render(tmp_path, "plain", BODY)
    got = _render(tmp_path, "got", BODY + line)
    for theme in ("light", "dark"):
        extra = _extra(got, plain, theme, WARN_SOFT)
        if banner:
            assert extra > 5000, (theme, extra)
            assert _extra(got, plain, theme, WARN) > 100, theme
        else:
            assert abs(extra) < 50, (theme, extra)


@_GUI
def test_a_slow_link_and_an_old_bus_are_one_banner_not_two(tmp_path):
    plain = _render(tmp_path, "plain", BODY)
    link = _render(tmp_path, "link", BODY + "nic_link=eth0|100|1000|1\n")
    pci = _render(tmp_path, "pci", BODY + "nic_bus=1\n")
    both = _render(tmp_path, "both", BODY + "nic_link=eth0|100|1000|1\nnic_bus=1\n")
    for theme in ("light", "dark"):
        one = max(_extra(link, plain, theme, WARN_SOFT), _extra(pci, plain, theme, WARN_SOFT))
        got = _extra(both, plain, theme, WARN_SOFT)
        assert one > 5000, (theme, one)
        assert 0.8 * one < got < 1.3 * one, (theme, got, one)      # stacked would be ~2x (and the bays would shrink twice)


@_GUI
def test_the_clone_screen_has_the_same_banner_on_the_same_flag(tmp_path):
    binary = tmp_path / "gui-bin"
    _render(tmp_path, "warm", BODY)           # builds the binary

    def clone(name, extra):
        state = tmp_path / f"{name}.txt"
        state.write_text(with_rules(HEAD + CLONE + extra), encoding="utf-8", newline="\n")
        _keep_fresh(state)
        _png(binary, tmp_path / name, state, "1280x800")
        return {t: _png_rgb(tmp_path / f"{name}-cloner-{t}.png") for t in ("light", "dark")}

    plain, pci, off = clone("cp", ""), clone("cb", "nic_bus=1\n"), clone("co", "nic_bus=0\n")
    both = clone("cl", "nic_link=eth0|100|1000|1\nnic_bus=1\n")
    for theme in ("light", "dark"):
        assert _extra(pci, plain, theme, WARN_SOFT) > 5000, theme
        assert abs(_extra(off, plain, theme, WARN_SOFT)) < 50, theme
        one = _extra(pci, plain, theme, WARN_SOFT)
        assert 0.8 * one < _extra(both, plain, theme, WARN_SOFT) < 1.3 * one, theme
