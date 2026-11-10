"""#2776 + #2777: the installer is used with the keyboard alone, start to finish.

#2776 (Nadav 09/11, on metal through a KVM whose mouse is not taken -- #2774,
closed, #683 stays): Tab did nothing on the disk screen -- ``stops()`` had no
stop for it -- and "boot from the disk", the summary's "שינוי" links and the
eye beside each password had no key at all. The contract checked here:

* Tab / Shift+Tab reach every control on every screen. On the disk screen the
  list and the RAID choice are one stop each (Up/Down and Left/Right choose in
  them, as before); landing never chooses a disk (#2147).
* Space presses the control in focus; Enter stays the screen's main action --
  except on a summary "שינוי" link, where Enter opens that step: a focused
  link never starts the install.
* The footer's key line says Tab on every screen, and Tab does move there.

#2777 (Nadav 09/11, metal, v0.91.7): static 10.0.0.2 / 255.255.255.0 would
not go on. Reproduced in QEMU with a NIC that has a DHCP lease: the fields are
seeded from the lease (#2096), the typing was appended to the seed, and the
engine refused "10.0.2.1510.0.0.2" (``invalid-address``). A field the focus
has just arrived at is now "selected": the first character replaces what it
holds, and Backspace empties it.

The driver is test_native_installer_keyboard.py's (install.c, #included).
"""

from __future__ import annotations

import re
from pathlib import Path

from test_native_installer_keyboard import (  # noqa: F401 -- `driver` is a fixture
    ADMIN, DISK, NATIVE, NETWORK, RC4_TWO_DISKS, ROLE, SUMMARY, _col, _press, _press_inventory, driver)

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

HOSTNAME, TIME, BACKUP, DONE, PROGRESS, FAILED = 4, 5, 6, 9, 10, 11


# --- #2776: the disk screen ------------------------------------------------------------

@NATIVE
def test_tab_walks_the_disk_screen_and_chooses_nothing_on_the_way(driver, tmp_path):
    """The demo: two disks + the media, RAID offered (metal), an existing install."""
    states, bridge = _press(driver, tmp_path, DISK, "TAB", "TAB", "TAB", "TAB", "TAB", "BTAB", "BTAB")
    assert _col(states, "focus") == ["none", "disks", "raid", "ack", "boot_local", "disks", "boot_local", "ack"]
    assert set(_col(states, "disk")) == {"0"} and set(_col(states, "ack")) == {"1"}
    assert len(set(_col(states, "raid"))) == 1, "walking past the RAID choice changed it"
    assert bridge == []


@NATIVE
def test_space_presses_boot_from_the_disk_and_enter_stays_next(driver, tmp_path):
    states, bridge = _press(driver, tmp_path, DISK, "BTAB", "SPACE")
    assert _col(states, "focus")[1] == "boot_local"
    assert bridge == ["boot-local"], "Space on the focused button presses it"
    assert states[-1]["ack"] == "1", "and does not untick 'I understand'"

    states, bridge = _press(driver, tmp_path, DISK, "BTAB", "ENTER")
    assert bridge == ["validate"] and states[-1]["view"] == str(ROLE), "Enter is 'next', as the key line says"


@NATIVE
def test_space_still_ticks_i_understand_from_the_list_and_from_the_box(driver, tmp_path):
    """The guard: #1206's Space on the disk screen is unchanged where it was."""
    states, _ = _press(driver, tmp_path, DISK, "SPACE", "TAB", "SPACE", "TAB", "TAB", "SPACE")
    assert _col(states, "focus")[-1] == "ack"
    assert _col(states, "ack") == ["1", "0", "0", "1", "1", "1", "0"]


@NATIVE
def test_with_no_disk_chosen_tab_chooses_none_and_skips_the_dead_box(driver, tmp_path):
    """#2147: two disks, none chosen. "I understand" is off (no hit box) until one is."""
    states, bridge, _ = _press_inventory(driver, tmp_path, RC4_TWO_DISKS, "TAB", "TAB", "BTAB")
    assert _col(states, "focus")[1:] == ["disks", "disks", "disks"]
    assert set(_col(states, "disk")) == {"-1"}
    assert bridge == ["inventory"]


@NATIVE
def test_up_down_still_choose_the_disk_after_tab(driver, tmp_path):
    states, _ = _press(driver, tmp_path, DISK, "TAB", "DOWN", "TAB", "LEFT")
    assert _col(states, "disk") == ["0", "0", "1", "1", "1"]
    assert _col(states, "focus")[-2] == "raid"


# --- #2776: the summary --------------------------------------------------------------

@NATIVE
def test_tab_reaches_every_change_link_and_enter_there_opens_the_step_not_the_install(driver, tmp_path):
    states, bridge = _press(driver, tmp_path, SUMMARY, *["TAB"] * 8)
    assert _col(states, "focus")[1:] == [f"change{i}" for i in range(7)] + ["change0"]
    assert bridge == []

    states, bridge = _press(driver, tmp_path, SUMMARY, "TAB", "TAB", "TAB", "ENTER")
    assert states[-1]["view"] == str(NETWORK), "row 2 is the network step"
    assert bridge == [], "a focused link never starts the install"

    states, bridge = _press(driver, tmp_path, SUMMARY, "BTAB", "SPACE")
    assert states[-1]["view"] == str(ADMIN) and bridge == []


@NATIVE
def test_enter_with_no_link_in_focus_still_applies(driver, tmp_path):
    """The guard: the summary's Enter is still "החל התקנה"."""
    states, bridge = _press(driver, tmp_path, SUMMARY, "ENTER")
    assert bridge == ["apply"] and states[-1]["view"] == str(PROGRESS)


# --- #2776: progress, failure, done ---------------------------------------------------

@NATIVE
def test_progress_details_open_and_scroll_from_the_keyboard(driver, tmp_path):
    states, _ = _press(driver, tmp_path, PROGRESS, "TAB", "ENTER", "DOWN", "DOWN", "UP", "SPACE")
    assert _col(states, "focus")[1] == "technical"
    assert _col(states, "technical") == ["0", "0", "1", "1", "1", "1", "0"]
    assert _col(states, "scroll") == ["0", "0", "0", "5", "10", "5", "5"]


@NATIVE
def test_failure_scrolls_retries_and_goes_back_from_the_keyboard(driver, tmp_path):
    states, _ = _press(driver, tmp_path, FAILED, "DOWN", "TAB", "SPACE")
    assert _col(states, "scroll")[1] == "5"
    assert _col(states, "focus")[2] == "retry"
    assert states[-1]["view"] == str(DISK), "Space on 'נסה שוב' retries"

    states, _ = _press(driver, tmp_path, FAILED, "ESC")
    assert states[-1]["view"] == str(DISK)


@NATIVE
def test_done_cancels_the_restart_from_the_focused_button(driver, tmp_path):
    states, bridge = _press(driver, tmp_path, DONE, "TAB", "SPACE", "TAB")
    assert _col(states, "focus")[1] == "cancel_reboot"
    assert _col(states, "reboot") == ["1", "1", "0", "0"]
    assert bridge == [], "cancelling never restarts"


# --- #2776: the admin screen's eyes -------------------------------------------------

@NATIVE
def test_the_eye_after_each_password_is_a_stop_and_space_shows_it(driver, tmp_path):
    states, _ = _press(driver, tmp_path, ADMIN, *["DOWN"] * 8)
    assert _col(states, "focus") == ["admin_user", "password", "show_password", "confirm", "show_confirm",
                                     "root_password", "show_root", "root_confirm", "show_root_confirm"]
    states, _ = _press(driver, tmp_path, ADMIN, "TAB", "TAB", "SPACE", "TAB", "TAB", "SPACE")
    assert _col(states, "shown")[-1] == "1100"


@NATIVE
def test_space_inside_a_password_is_still_a_character(driver, tmp_path):
    """The guard: only the eye takes Space; a password may hold a space."""
    states, _ = _press(driver, tmp_path, ADMIN, "TAB", "SPACE")
    assert states[-1]["shown"] == "0000" and states[-1]["focus"] == "password"


# --- #2776: every screen, Tab moves, and the key line says so -------------------------

def _key_lines() -> dict[int, str]:
    """install_keys' text for each view, read from the source (every literal of a case)."""
    body = (SRC / "screens_install.c").read_text(encoding="utf-8")
    body = body[body.index("static const char *install_keys("):]
    body = body[:body.index("\n}\n")]
    names = {"INSTALL_DISK": DISK, "INSTALL_ROLE": ROLE, "INSTALL_NETWORK": NETWORK, "INSTALL_HOSTNAME": HOSTNAME,
             "INSTALL_TIME": TIME, "INSTALL_BACKUP": BACKUP, "INSTALL_ADMIN": ADMIN, "INSTALL_SUMMARY": SUMMARY,
             "INSTALL_PROGRESS": PROGRESS, "INSTALL_FAILED": FAILED, "INSTALL_DONE": DONE}
    lines: dict[int, str] = {}
    for cases, ret in re.findall(r"((?:case INSTALL_\w+:)+)\s*return\s*([^;]*);", body):
        for name in re.findall(r"INSTALL_\w+", cases):
            lines[names[name]] = " ".join(re.findall(r'"([^"]*)"', ret))
    default = re.search(r"default:\s*return\s*([^;]*);", body)
    for view in names.values():
        lines.setdefault(view, " ".join(re.findall(r'"([^"]*)"', default.group(1))) if default else "")
    return lines


@NATIVE
def test_on_every_screen_tab_moves_and_the_key_line_says_tab(driver, tmp_path):
    lines = _key_lines()
    for view in (DISK, ROLE, NETWORK, HOSTNAME, TIME, BACKUP, ADMIN, SUMMARY, PROGRESS, FAILED, DONE):
        states, _ = _press(driver, tmp_path, view, f"goto={view}", "TAB")
        # a screen with one control keeps the focus on it (#2773's name step); none = Tab is dead there
        assert states[-1]["focus"] != "none", f"view {view}: Tab reached no control"
        assert "Tab" in lines[view], f"view {view}: the key line does not say Tab: {lines[view]!r}"


@NATIVE
def test_the_whole_install_from_the_keyboard_alone(driver, tmp_path):
    """Disk -> summary -> "change" and back -> apply -> details, with no click at all."""
    walk = ["SPACE", "SPACE", "ENTER",                 # untick, tick "I understand", next
            "ENTER", "ENTER", "ENTER", "ENTER", "ENTER", "ENTER",   # role .. admin (the demo's answers)
            "TAB", "TAB", "ENTER",                     # summary: "שינוי" on the role -> the role step
            "ENTER", "ENTER", "ENTER", "ENTER", "ENTER", "ENTER",   # back to the summary
            "ENTER",                                   # apply
            "SPACE", "DOWN"]                           # the details, scrolled
    states, bridge = _press(driver, tmp_path, DISK, *walk)
    views = _col(states, "view")
    assert views[3] == str(ROLE) and views[9] == str(SUMMARY)
    assert views[12] == str(ROLE) and views[18] == str(SUMMARY)
    assert views[19] == str(PROGRESS)
    assert states[-1]["technical"] == "1" and states[-1]["scroll"] == "5"
    assert bridge.count("apply") == 1 and bridge[-1] == "apply"


# --- #2777: a seeded field is replaced by typing, not appended to ---------------------

@NATIVE
def test_static_typed_over_the_dhcp_seed_is_what_was_typed(driver, tmp_path):
    """Nadav's case: the NIC had a lease, so the static fields start with it."""
    states, _ = _press(driver, tmp_path, NETWORK, "SPACE", "T:10.0.0.2", "TAB", "T:255.255.255.0")
    assert states[1]["address"] == "10.44.10.37", "the demo NIC's lease seeds the field (#2096)"
    assert states[2]["address"] == "10.0.0.2"
    assert states[-1]["netmask"] == "255.255.255.0"


@NATIVE
def test_backspace_on_a_seeded_gateway_empties_it(driver, tmp_path):
    """No gateway at all (Nadav may install without one): one Backspace per field."""
    states, _ = _press(driver, tmp_path, NETWORK, "SPACE", "TAB", "TAB", "BKSP", "TAB", "BKSP")
    assert _col(states, "focus")[-1] == "dns"
    assert states[-1]["gateway"] == "" and states[-1]["dns"] == ""
    assert states[-1]["address"] == "10.44.10.37", "fields that were passed by are kept"


@NATIVE
def test_after_the_first_key_typing_and_backspace_edit_as_before(driver, tmp_path):
    """The guard: only the first key replaces; the rest append and Backspace takes one."""
    states, _ = _press(driver, tmp_path, NETWORK, "SPACE", "T:10.0.0.25", "BKSP", "T:9")
    assert _col(states, "address")[-3:] == ["10.0.0.25", "10.0.0.2", "10.0.0.29"]


@NATIVE
def test_coming_back_to_a_field_selects_it_again(driver, tmp_path):
    states, _ = _press(driver, tmp_path, NETWORK, "SPACE", "T:10.0.0.2", "TAB", "BTAB", "T:10.0.0.3")
    assert states[-1]["focus"] == "address" and states[-1]["address"] == "10.0.0.3"


@NATIVE
def test_the_hostname_default_is_replaced_by_the_typed_name(driver, tmp_path):
    states, _ = _press(driver, tmp_path, HOSTNAME, "T:srv-1")
    assert states[-1]["hostname"] == "srv-1"


def test_a_selected_field_is_drawn_selected():
    """What the person sees must say that typing replaces: the field's text on a highlight."""
    frame = (SRC / "screens_install.c").read_text(encoding="utf-8")
    assert "install_field_selected(a,id)" in frame
