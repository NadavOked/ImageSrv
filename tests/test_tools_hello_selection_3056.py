"""#3056 -- the operator's toolbox choice reaches the station in the hello answer.

No build path ever packed `tools-selection.json` into the initrd, so the station had no
selection and offered nothing, whatever the operator ticked. The build machine's hello now
carries `tools_selection` (the "build" ids) while the edition flag is on, and
`tools_selection_ids` reads it first; without the field (an older server) the file is used as before.

What these tests hold: the answer carries the saved ids for the build machine only and only
when the toolbox is on; the station lists exactly those ids with NO file at all; the answer wins
over a stale file; an empty list is "nothing selected" (not "file missing"); no field = the file.
"""

from __future__ import annotations

import json

from native import requires_native
from test_tools_framework import BASH, DISK_MODULE, NET_MODULE, last, setup, sh


# --- the station side -------------------------------------------------------------


def _hello_selection(run, selection):
    (run / "response.json").write_text(
        json.dumps({"role": "build", "tools": True, "tools_selection": selection}), encoding="utf-8", newline="\n")


def _listed(out):
    return [line.split("|")[0] for line in out.stdout.splitlines() if "|" in line]


@requires_native(("bash", BASH), "jq", why="tools_selection_ids reads the hello answer with jq")
def test_the_station_lists_the_ids_of_the_hello_answer_with_no_selection_file(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE})
    (tmp_path / "tools-selection.json").unlink()
    _hello_selection(run, ["wipe", "ping"])
    out = sh(pre + "tools_list; echo rc=$?")
    assert last(out) == "rc=0", out.stderr
    assert _listed(out) == ["wipe", "ping"], (out.stdout, out.stderr)
    assert "no selection file" not in out.stderr


@requires_native(("bash", BASH), "jq", why="tools_selection_ids reads the hello answer with jq")
def test_the_hello_answer_wins_over_a_stale_file(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE})     # the file names every id
    _hello_selection(run, ["ping"])
    out = sh(pre + "tools_list")
    assert _listed(out) == ["ping"], (out.stdout, out.stderr)


@requires_native(("bash", BASH), "jq", why="tools_selection_ids reads the hello answer with jq")
def test_an_empty_list_in_the_hello_answer_is_nothing_selected_not_a_missing_file(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE})
    _hello_selection(run, [])
    out = sh(pre + "tools_list; echo rc=$?")
    assert last(out) == "rc=0"
    assert _listed(out) == []
    assert "no selection file" not in out.stderr


@requires_native(("bash", BASH))
def test_a_hello_answer_without_the_field_keeps_using_the_file(tmp_path):
    _, _, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE}, selection=["wipe"])
    out = sh(pre + "tools_list")
    assert _listed(out) == ["wipe"], (out.stdout, out.stderr)


# --- the server side --------------------------------------------------------------


def _save(server, ids):
    from server.db import set_setting
    set_setting(server["ctx"].conn, "tools.selection", json.dumps({"build": ids, "student": ["x"]}))


def test_the_build_machine_hello_carries_the_saved_ids_when_the_toolbox_is_on(server, monkeypatch):
    from server.core import capabilities
    from test_server_api import hello, register_build_machine
    monkeypatch.setattr(capabilities, "TOOLS", True)
    mac = register_build_machine(server)
    _save(server, ["win-users", "nwipe"])
    assert hello(server, mac)["tools_selection"] == ["win-users", "nwipe"]


def test_nothing_saved_is_an_empty_list_not_a_missing_field(server, monkeypatch):
    from server.core import capabilities
    from test_server_api import hello, register_build_machine
    monkeypatch.setattr(capabilities, "TOOLS", True)
    assert hello(server, register_build_machine(server))["tools_selection"] == []


def test_the_field_is_absent_when_the_toolbox_is_off(server, monkeypatch):
    from server.core import capabilities
    from test_server_api import hello, register_build_machine
    monkeypatch.setattr(capabilities, "TOOLS", False)
    mac = register_build_machine(server)
    _save(server, ["nwipe"])
    assert "tools_selection" not in hello(server, mac)


def test_a_classroom_machine_does_not_get_the_field(server, monkeypatch):
    from conftest import setup_classroom
    from server.core import capabilities
    from test_server_api import hello
    monkeypatch.setattr(capabilities, "TOOLS", True)
    ids = setup_classroom(server)
    _save(server, ["nwipe"])
    assert "tools_selection" not in hello(server, ids["mac1"])
