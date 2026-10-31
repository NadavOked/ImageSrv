"""#1819 — the imagesrv-server unit sets its own open-files limit.

systemd's default soft limit is 1024 and Python does not raise it; agents,
monitor WebSockets, image pulls and a sqlite connection per thread in a
class round can exceed it, and ``accept`` then fails with EMFILE. The unit
carries ``LimitNOFILE`` -- a number, not ``infinity`` (the issue forbids it),
in the ``[Service]`` section where systemd reads it, and set once.

The unit is read the way systemd reads it: sections, ``#``/``;`` comments,
and backslash continuation lines -- a ``LimitNOFILE=`` inside a comment or
under ``[Install]`` is not a limit.

**Negative control** (in the commit): on the unit before the fix the test
fails with ``LimitNOFILE`` missing from ``[Service]``.
"""

from __future__ import annotations

from pathlib import Path

UNIT = Path(__file__).resolve().parents[1] / "deploy" / "imagesrv-server.service"


def directives(text: str) -> list[tuple[str, str, str]]:
    """(section, key, value) in file order -- systemd.syntax(7), enough of it."""
    out, section, pending = [], None, ""
    for raw in text.splitlines():
        line = pending + raw.strip() if pending else raw.strip()
        pending = ""
        if not line or line[0] in "#;":
            continue
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, sep, value = line.partition("=")
        assert sep, f"not a directive: {line!r}"
        out.append((section, key.strip(), value.strip()))
    return out


def nofile(text: str) -> list[str]:
    return [v for s, k, v in directives(text) if s == "Service" and k == "LimitNOFILE"]


def test_the_server_unit_raises_the_open_files_limit():
    values = nofile(UNIT.read_text(encoding="utf-8"))
    assert len(values) == 1, f"LimitNOFILE in [Service]: {values}"
    soft, _, hard = values[0].partition(":")
    assert soft.isdigit() and (not hard or hard.isdigit()), f"a number, not infinity: {values[0]!r}"
    assert int(soft) >= 65536, values[0]
    assert not hard or int(hard) >= int(soft), values[0]


def test_the_reader_does_not_count_a_comment_or_another_section():
    """The guard on the reader itself: a commented or misplaced limit is no limit."""
    assert nofile("[Service]\n# LimitNOFILE=65536\n[Install]\nLimitNOFILE=65536\n") == []
    assert nofile("[Service]\nExecStart=/x \\n  --y\nLimitNOFILE=65536\n") == ["65536"]
