"""#1412: an ISO built with ``--lab`` says so -- "ISO מעבדה — לא למכללה".

``build-iso.sh`` writes ``lab_overlay`` into ``imagesrv-iso.json`` (the gate,
``tools/iso/initrd-lab-gate.sh``); until now nothing read it. Three readers:

* the server screen (DCUI) -- ``data.lab_iso_state`` reads the installer's copy,
  ``/etc/imagesrv/iso-release.json``; ``view`` turns it into an orange card, and
  the text screen (80x25, English) into a line under the title;
* the live installer -- ``installer/gui-bridge.sh inventory`` emits
  ``lab_overlay=lab|clean|unchecked`` (tests in test_installer_gui_bridge.py, sh+jq);
* the native installer GUI draws a strip above every step
  (test_native_installer_lab_1412.py, Linux + cairo).

Principle 5 everywhere: only a JSON boolean is an answer. A manifest that
cannot be read, or carries no boolean, is "not checked" -- a warning, never
"clean". Here: the data, the view and the text screen; they run on every machine.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from server.dcui import app as dcui_app
from server.dcui import samples, view
from server.dcui.data import (LAB_ISO_CLEAN, LAB_ISO_LAB, LAB_ISO_UNCHECKED, Snapshot,
                              lab_iso_state)

REPO = Path(__file__).resolve().parent.parent


# ---- the data: the manifest's boolean, and nothing else -----------------------------

def _manifest(tmp_path: Path, content: str | bytes) -> Path:
    path = tmp_path / "iso-release.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("content,state", [
    (json.dumps({"imagesrv_tag": "v0.85.0-lab", "lab_overlay": True}), LAB_ISO_LAB),
    (json.dumps({"imagesrv_tag": "v0.85.0", "lab_overlay": False}), LAB_ISO_CLEAN),
])
def test_the_manifests_boolean_is_the_answer(tmp_path, content, state):
    assert lab_iso_state(_manifest(tmp_path, content)) == state


@pytest.mark.parametrize("content", [
    json.dumps({"imagesrv_tag": "v0.74.2"}),          # an ISO from before the field
    json.dumps({"lab_overlay": "false"}),             # a string is not an answer
    json.dumps({"lab_overlay": None}),
    json.dumps({"lab_overlay": 0}),
    json.dumps([{"lab_overlay": False}]),             # not an object
    "{not json",
    "",
    b"\xff\xfe{\"lab_overlay\": false}",              # not UTF-8
])
def test_anything_but_a_boolean_is_unchecked_never_clean(tmp_path, content):
    assert lab_iso_state(_manifest(tmp_path, content)) == LAB_ISO_UNCHECKED


def test_a_manifest_that_cannot_be_read_is_unchecked(tmp_path):
    # a directory where the file should be: IsADirectoryError (Linux) /
    # PermissionError (Windows) -- an OSError that is not "no such file"
    path = tmp_path / "iso-release.json"
    path.mkdir()
    assert lab_iso_state(path) == LAB_ISO_UNCHECKED


def test_no_manifest_at_all_is_no_iso_not_a_warning(tmp_path):
    """setup-boot-server.sh installs without an ISO and without the file: there
    is no ISO to ask about. Only "no such file" means that."""
    assert lab_iso_state(tmp_path / "iso-release.json") == ""


def test_collect_snapshot_reads_the_same_manifest_as_the_rest_of_the_screen():
    text = (REPO / "server" / "dcui" / "data.py").read_text(encoding="utf-8")
    body = text[text.index("def collect_snapshot("):text.index("def demo_snapshot(")]
    assert "lab_iso=lab_iso_state(manifest_file)," in body


# ---- the view: an orange card, never "השרת תקין" -----------------------------------

def _frame(snapshot: Snapshot) -> str:
    runtime = samples._runtime(snapshot, samples.PRIMARY_EXTRAS)
    return view.render(runtime, samples.NOW, samples.NOW, samples.CLOCK, samples.TODAY, 47)


def _records(frame: str, kind: str) -> list[list[str]]:
    return [line.split("\t") for line in frame.rstrip("\n").split("\n") if line.split("\t")[0] == kind]


def test_a_lab_iso_is_an_orange_card_that_says_not_for_the_college():
    frame = _frame(replace(samples.PRIMARY, lab_iso=LAB_ISO_LAB))
    (prob,) = _records(frame, "prob")
    assert prob[1] == "warn" and prob[3] == "ISO מעבדה — לא למכללה"
    assert "SSH" in prob[4] and "hooks" in prob[4]
    (hero,) = _records(frame, "hero")
    assert hero[1] == "warn" and hero[2] == "השרת דורש טיפול · בעיה אחת"
    assert hero[3] == "השרת <b>אינו מיועד למכללה</b>."
    assert _records(frame, "sb")[0][1:3] == ["warn", "דורש טיפול · 1"]


def test_an_unchecked_manifest_is_its_own_orange_card():
    frame = _frame(replace(samples.PRIMARY, lab_iso=LAB_ISO_UNCHECKED))
    (prob,) = _records(frame, "prob")
    assert prob[1] == "warn" and prob[3] == "לא נבדק אם השרת הותקן מ־ISO מעבדה"
    assert "/etc/imagesrv/iso-release.json" in prob[4] and "lab_overlay" in prob[4]
    assert _records(frame, "hero")[0][1] == "warn"


@pytest.mark.parametrize("state", [LAB_ISO_CLEAN, ""])
def test_a_clean_iso_or_none_adds_no_card(state):
    frame = _frame(replace(samples.PRIMARY, lab_iso=state))
    assert not _records(frame, "prob")
    assert _records(frame, "hero")[0][1:3] == ["ok", "השרת תקין"]


def test_a_lab_iso_never_turns_the_header_red_by_itself():
    """#2144 (Nadav 04/11): red only for what stops the server's work. A lab
    ISO does not stop it -- orange, like every warning card."""
    assert view.lab_iso_problem(replace(samples.PRIMARY, lab_iso=LAB_ISO_LAB)).sev == "warn"
    assert view.lab_iso_problem(replace(samples.PRIMARY, lab_iso=LAB_ISO_UNCHECKED)).sev == "warn"


def test_the_samples_carry_both_states_for_the_native_renders():
    frames = samples.frames()
    assert _records(frames["17-lab-iso"], "prob")[0][3] == "ISO מעבדה — לא למכללה"
    assert _records(frames["17b-lab-iso-unchecked"], "prob")[0][3] == "לא נבדק אם השרת הותקן מ־ISO מעבדה"


# ---- the text screen (80x25, English): a line under the title ------------------------

@pytest.mark.parametrize("state,words", [
    (LAB_ISO_LAB, "LAB ISO - NOT FOR THE COLLEGE"),
    (LAB_ISO_UNCHECKED, "NOT CHECKED for a lab ISO"),
])
def test_the_text_screen_says_it_on_the_line_under_the_title(state, words):
    lines = dcui_app.render_main(replace(samples.PRIMARY, lab_iso=state))
    assert words in lines[3]
    assert all(len(line) == 80 for line in lines)


@pytest.mark.parametrize("state", [LAB_ISO_CLEAN, ""])
def test_the_text_screen_is_unchanged_for_a_clean_iso(state):
    lines = dcui_app.render_main(replace(samples.PRIMARY, lab_iso=state))
    assert lines == dcui_app.render_main(samples.PRIMARY)
    assert not re.search(r"LAB|lab ISO", "\n".join(lines))


# ---- the contract ------------------------------------------------------------------

def test_the_contract_documents_the_three_readers():
    doc = (REPO / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    section = doc[doc.index("## 44. ISO מעבדה"):]
    assert r"lab_overlay=lab\|clean\|unchecked" in section   # a markdown table cell
    assert "iso-release.json" in section and "imagesrv-iso.json" in section
