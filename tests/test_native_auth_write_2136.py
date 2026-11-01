"""#2136: failed input delivery must fail login, following the pattern of #2108.

The driver extracts the real run_auth from main.c and ignores SIGPIPE as the
GUI does. A command that closes stdin and exits 0 must fail authentication;
commands that read the same input, or check a short password, must succeed.
The large password is four times a Linux pipe buffer (64 KiB), so its write
must meet the closed end regardless of scheduling, rather than fit in the pipe.

Negative control: on the code before #2136 test 1 prints rc=0.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
BIG = 256 * 1024

NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    posix=True, why="run_auth is built and run on Linux only",
)


def _run_auth_source() -> str:
    source = (SRC / "main.c").read_text(encoding="utf-8")
    signature = "static int run_auth("
    start = source.index(signature)
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        depth += (source[j] == "{") - (source[j] == "}")
        if depth == 0:
            return source[start:j + 1]
    raise AssertionError(f"unclosed function: {signature}")


DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/wait.h>

static void theme_login_answer(const char *buf, char *role, size_t n, char *saved, size_t sn) {
    if (n) {
        size_t len = strcspn(buf, "\r\n");
        if (len >= n) len = n - 1;
        memcpy(role, buf, len);
        role[len] = 0;
    }
    if (sn) saved[0] = 0;
}

%(run_auth)s

int main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    if (argc != 3) return 2;
    size_t n = strtoul(argv[2], NULL, 10);
    char *pass = malloc(n + 1);
    if (!pass) return 2;
    memset(pass, 'x', n);
    pass[n] = 0;
    char role[64] = "", saved[16] = "";
    int rc = run_auth(argv[1], "admin", pass, role, sizeof role, saved, sizeof saved);
    free(pass);
    printf("rc=%%d\nrole=%%s\n", rc, role);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("auth-write")
    source = tmp / "auth_write.c"
    source.write_text(DRIVER % {"run_auth": _run_auth_source()}, encoding="utf-8")
    binary = tmp / "auth_write"
    build = subprocess.run(
        [shutil.which("cc") or shutil.which("gcc"), "-O2", "-Wall", "-Wextra",
         "-std=c11", "-D_GNU_SOURCE", "-o", str(binary), str(source)],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    return binary


def _call(driver: Path, command: str, size: int) -> dict[str, str]:
    proc = subprocess.run(
        [str(driver), command, str(size)], capture_output=True,
        encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


@NATIVE
def test_a_login_command_that_closed_its_input_is_a_failed_login(driver, tmp_path):
    log = tmp_path / "auth.log"
    out = _call(driver, f'exec <&-; sleep 1; echo ran >"{log}"; echo admin', BIG)
    assert log.read_text().split() == ["ran"], "the command really ran and exited 0"
    assert out["rc"] == "-1", \
        "a password that never reached the login command was read as a successful login"


@NATIVE
def test_a_login_command_that_reads_the_same_input_succeeds(driver):
    """The guard: the size alone is not the failure."""
    out = _call(driver, "cat >/dev/null; echo admin", BIG)
    assert out == {"rc": "0", "role": "admin"}


@NATIVE
def test_a_short_password_that_is_read_and_checked_signs_in(driver):
    out = _call(driver, 'read u; read p; [ "$u" = admin ] && [ "${#p}" = 5 ] && echo admin', 5)
    assert out == {"rc": "0", "role": "admin"}


def test_the_driver_runs_the_real_run_auth():
    assert "fdopen(in_p[1]" in _run_auth_source()
