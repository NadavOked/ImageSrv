"""Exercise the root shell selected by the initramfs builder with safe power commands."""

import subprocess
from pathlib import Path

import pytest

from native import requires_native

BUILDER = Path(__file__).resolve().parents[1] / "tools" / "build_initramfs.sh"
pytestmark = requires_native("bash", "dash", why="the builder and the overlay login shell")


@pytest.mark.parametrize("overlay", [False, True])
def test_root_shell_dispatches_power_commands(tmp_path, overlay):
    builder = BUILDER.read_text(encoding="utf-8")
    account = builder.split("# --- the account ", 1)[1].split("\n", 1)[1]
    account = account.split('\nchmod 0700 "$ROOT/root"', 1)[0]
    copy_bin = "copy_bin() {" + builder.split("copy_bin() {", 1)[1].split("\n}", 1)[0] + "\n}"

    # Use the real binary packing and account setup, leaving host libraries out
    # of the fixture. The old shell models BusyBox's observed applet dispatch.
    script = f"""#!/bin/bash
set -eu
PATH=/usr/bin:/bin
ROOT="$PWD/root"
EXTRA_OVERLAY={"$PWD/overlay" if overlay else "''"}
mkdir -p "$ROOT"/{{bin,usr/bin,etc}}
declare -A PACKED_FROM=()
copy_libs() {{ :; }}
{copy_bin}
{account}

printf '#!/bin/sh\nprintf "busybox applets\\n"\n' > "$ROOT/bin/sh"
for name in reboot poweroff; do
    printf '#!/bin/sh\nprintf "wrapper: %s\\n"\n' "$name" > "$ROOT/bin/$name"
done
chmod +x "$ROOT/bin/"*
IFS=: read -r user password uid gid gecos home shell < "$ROOT/etc/passwd"
# dropbear refuses a login shell missing from /etc/shells (metal, 28/11).
grep -qxF "$shell" "$ROOT/etc/shells" || {{ echo "no $shell in /etc/shells" >&2; exit 3; }}
PATH="$ROOT/bin" "$ROOT$shell" -c 'reboot && poweroff'
"""
    (tmp_path / "check.sh").write_text(script, encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["bash", "check.sh"], cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    expected = ["wrapper: reboot", "wrapper: poweroff"] if overlay else ["busybox applets"]
    assert result.stdout.splitlines() == expected
