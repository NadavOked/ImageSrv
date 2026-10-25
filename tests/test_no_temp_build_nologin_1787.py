"""#1787: the temporary "build machine without a sign-in" switch (#1786) is
out of the product, and stays out.

Nadav, 19/10: "למחוק לפני v1". The switch let a build machine restore and
capture with no console session, so a leftover line is a way around the
sign-in -- not dead code. This scans the product trees for its names.

`install/vmupdate-host.sh` gives a system user the shell `/usr/sbin/nologin`;
that is unrelated, and the patterns below do not match it (checked here too).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_DIRS = ("agent", "server", "native-gui", "install")

# Split so that a repo-wide grep for the marker finds nothing once it is gone.
MARKER = "TEMP-BUILD-" + "NOLOGIN"
PATTERNS = (
    MARKER,
    "build_nologin",                # server/build_nologin.py, ui.build_nologin, the journal event
    "nologin.sh",                   # agent/lib/nologin.sh
    "nologin_",                     # nologin_active / nologin_capture / nologin_folders / ...
    "agent/nologin/",               # /api/v1/agent/nologin/{folders,capture}
    "--role nologin",               # the kiosk starting the GUI signed in as nobody
    "GUI_ROLE=nologin",
    '"nologin"',                    # the role string in native-gui
)


def _hits(text: str) -> list[str]:
    return [p for p in PATTERNS if p in text]


def _product_files() -> list[Path]:
    files = []
    for d in PRODUCT_DIRS:
        base = ROOT / d
        assert base.is_dir(), f"{d}/ is missing: the scan would be empty, not clean"
        files += [p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return files


def test_no_trace_of_the_temporary_switch_in_the_product():
    files = _product_files()
    names = {p.relative_to(ROOT).as_posix() for p in files}
    # Positive evidence that the scan saw the files the switch lived in.
    for must in ("agent/lib/guibridge.sh", "server/api.py", "server/hello.py",
                 "native-gui/src/main.c", "install/vmupdate-host.sh"):
        assert must in names, f"the scan did not reach {must}"
    found = []
    for path in files:
        text = path.read_bytes().decode("utf-8", errors="replace")
        for pattern in _hits(text):
            found.append(f"{path.relative_to(ROOT).as_posix()}: {pattern}")
    assert not found, "the temporary sign-in-free switch is still in the product:\n" + "\n".join(found)


def test_the_switch_files_are_gone():
    for gone in ("agent/lib/nologin.sh", "server/build_nologin.py"):
        assert not (ROOT / gone).exists(), gone


def test_the_system_user_shell_is_not_caught():
    text = (ROOT / "install" / "vmupdate-host.sh").read_text(encoding="utf-8")
    assert "/usr/sbin/nologin" in text   # still there, untouched
    assert _hits('        --shell /usr/sbin/nologin "$VM_USER" \\') == []
    # ...and every pattern does catch the line it is there for.
    for sample in ('. "$LIB_DIR/nologin.sh"', "from . import build_nologin",
                   "nologin_active", '"$SERVER/api/v1/agent/nologin/folders"',
                   "set -- --signed-in x --role nologin", "GUI_ROLE=nologin",
                   'strcmp(role, "nologin")', "# " + MARKER):
        assert _hits(sample), sample
