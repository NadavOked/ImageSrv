"""#1364 -- the graphical server screen without its fonts: the text screen said
"imagesrv-station-gui exited before opening the screen (exit 1)" and not why.

``main.c`` checks the IBM Plex families before ``dcui_main`` runs, so the
``FAIL <why>`` that #1362 added to the protocol was never sent: the reason went
to stderr (the journal) and the operator saw an exit code. The check now also
answers ``FAIL font family not found: ...`` on stdout in ``--screen dcui`` -- and
still draws nothing in another face (``text_fonts_present`` is untouched).

The real binary is run against a fontconfig with no fonts in it, and the real
consumer (``server.dcui.gui.Session``) reads what the text screen would show.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.dcui import gui
from test_cloner_gui import BASH, GUI, _build_gui, _pkgconfig

needs_cc = requires_native(
    ("bash", BASH),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

WHY = "font family not found: IBM Plex Sans Hebrew, IBM Plex Mono"


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("dcui1364"))


@pytest.fixture
def no_fonts(tmp_path, monkeypatch) -> dict[str, str]:
    """A fontconfig whose only directory is empty: no family at all."""
    empty = tmp_path / "no-fonts"; empty.mkdir()
    conf = tmp_path / "fonts.conf"
    conf.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                    f'<dir>{empty.as_posix()}</dir><cachedir>{(tmp_path / "cache").as_posix()}</cachedir></fontconfig>',
                    encoding="utf-8", newline="\n")
    monkeypatch.setenv("FONTCONFIG_FILE", str(conf))
    return {"FONTCONFIG_FILE": str(conf)}


def _run(binary: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=60, stdin=subprocess.DEVNULL, env={**os.environ, **env})


@needs_cc
def test_missing_fonts_answer_fail_with_the_reason_on_stdout(binary, no_fonts):
    out = _run(binary, no_fonts, "--screen", "dcui")
    assert out.returncode == 1, out
    assert out.stdout == f"FAIL {WHY}\n", out.stdout           # the protocol line, nothing else
    assert WHY in out.stderr, out.stderr                        # the journal keeps saying it too


@needs_cc
def test_the_text_screen_is_given_the_reason_not_an_exit_code(binary, no_fonts):
    session = gui.Session([str(binary), "--screen", "dcui"])
    why = session.wait_ready(seconds=20)
    session.stop()
    assert why == WHY, why
    assert "exited before opening the screen" not in why


@needs_cc
def test_another_invocation_does_not_speak_the_dcui_protocol(binary, no_fonts, tmp_path):
    """The line is for ``--screen dcui`` only: a render (``--png``) keeps stdout empty."""
    out = _run(binary, no_fonts, "--screen", "dcui", "--png", str(tmp_path / "x"))
    assert out.returncode == 1 and out.stdout == "", out
    out = _run(binary, no_fonts, "--png", str(tmp_path / "y"))
    assert out.returncode == 1 and out.stdout == "", out
