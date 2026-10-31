"""#1499 (Nadav 16/10): the native GUI opens in the signed-in user's saved theme,
and a switch made there is saved for that user.

The agent's half, in the real shell code (`guibridge.sh` + `buildmenu.sh`):

* `gui_auth` -- the `--auth-cmd` of the GUI -- prints the role, then the
  user's saved theme from the login answer (`light`/`dark`; `auto`, absent or
  foreign = `auto`, which the GUI draws as its light default).
* the `theme-light`/`theme-dark` records the GUI emits while signed in become
  `PUT /api/console/me/theme` with the GUI's session; anything but 200 is a
  visible error, not a silent "saved".

The server half is `tests/test_user_theme.py`, the GUI half
`tests/test_native_login_theme.py`.

Negative control: on the code before #1499 `gui_auth` prints the role only
and `gui_saved_theme`/`gui_theme_save` do not exist, so every test here fails.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, REPO, posix

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

#: Windows has no jq: a stand-in that knows exactly the two expressions this
#: path runs and fails (rc 5, like a jq compile error) on anything else.
JQ_STAND_IN = r'''
import json, sys
args = [a for a in sys.argv[1:] if not a.startswith("-")]
expr, path = args[0], args[1]
doc = json.load(open(path, encoding="utf-8"))
if expr.startswith(".role | select("):
    role = doc.get("role")
    if role in ("admin", "deploy"):
        print(role); sys.exit(0)
    sys.exit(1)
if expr.startswith("if .theme =="):
    t = doc.get("theme")
    print(t if t in ("light", "dark") else "auto"); sys.exit(0)
sys.exit(5)
'''


def run(tmp_path: Path, body: str, login: dict | None, code: str = "200") -> tuple[str, int, Path]:
    rd = tmp_path / "run"
    (rd / "gui").mkdir(parents=True, exist_ok=True)
    answer = rd / "answer.json"
    answer.write_text(json.dumps(login) if login is not None else "", encoding="utf-8")
    jq = ""
    if shutil.which("jq") is None:
        stand_in = tmp_path / "jq_stand_in.py"
        stand_in.write_text(JQ_STAND_IN, encoding="utf-8")
        jq = f'jq() {{ {shlex.quote(posix(Path(sys.executable)))} {shlex.quote(posix(stand_in))} "$@"; }}; '
    script = (
        f"export RUN_DIR={shlex.quote(posix(rd))} GUI_DIR={shlex.quote(posix(rd / 'gui'))} "
        f"LOG_FILE={shlex.quote(posix(rd / 'agent.log'))} SERVER=http://srv MAC=aa:bb "
        f"CONSOLE_JAR={shlex.quote(posix(rd / 'gui' / 'console.jar'))} "
        "IMAGESRV_TEST=1 HTTP_TIMEOUT=1 HTTP_RETRIES=0; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f". {posix(AGENT)}/lib/gui/buildmenu.sh; . {posix(AGENT)}/lib/gui/guibridge.sh; "
        + jq
        # the login itself is the server's (test_user_theme.py); here it saved its answer
        + f'console_signin() {{ cp {shlex.quote(posix(answer))} "$RUN_DIR/console_login.json"; }}; '
        + 'curl() { echo "CURL $*" >> "$RUN_DIR/curl.log"; '
        + '  for a in "$@"; do case "$a" in @*) cat "${a#@}" >> "$RUN_DIR/curl.body" ;; esac; done; '
        + f'  printf {code}; }}; '
        + body
    )
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=60)
    return proc.stdout, proc.returncode, rd


AUTH = 'printf "noc\\nsecret\\n" | gui_auth'


@pytest.mark.parametrize("saved,shown", [("light", "light"), ("dark", "dark"), ("auto", "auto")])
def test_the_auth_answer_is_the_role_then_the_saved_theme(tmp_path, saved, shown):
    out, rc, _ = run(tmp_path, AUTH, {"username": "noc", "role": "admin", "theme": saved})
    assert rc == 0, out
    assert out.splitlines() == ["admin", shown], out


def test_deploy_gets_its_theme_too(tmp_path):
    out, rc, _ = run(tmp_path, AUTH, {"username": "labtech", "role": "deploy", "theme": "dark"})
    assert rc == 0 and out.splitlines() == ["deploy", "dark"], out


@pytest.mark.parametrize("login", [
    {"username": "noc", "role": "admin"},                      # a server from before #1499
    {"username": "noc", "role": "admin", "theme": "blue"},     # a value nobody defined
], ids=["no-theme", "foreign"])
def test_an_unknown_theme_is_the_default_never_a_guess(tmp_path, login):
    out, rc, _ = run(tmp_path, AUTH, login)
    assert rc == 0 and out.splitlines() == ["admin", "auto"], out


def test_a_refused_login_prints_nothing(tmp_path):
    out, rc, _ = run(tmp_path, AUTH, {"detail": "no"})
    assert rc != 0 and out == "", out


def test_a_switch_is_saved_with_the_gui_session(tmp_path):
    out, rc, rd = run(tmp_path, 'gui_theme_save dark; echo "rc=$?"', None)
    assert "rc=0" in out, out
    call = (rd / "curl.log").read_text(encoding="utf-8")
    assert "-X PUT" in call and "http://srv/api/console/me/theme" in call, call
    assert f"-b {posix(rd / 'gui' / 'console.jar')}" in call, call
    assert json.loads((rd / "curl.body").read_text(encoding="utf-8")) == {"theme": "dark"}


def test_a_refused_save_is_a_visible_error(tmp_path):
    out, rc, rd = run(tmp_path, 'gui_theme_save light; echo "rc=$?"', None, code="401")
    assert "rc=1" in out, out
    assert "ערכת הנושא לא נשמרה (http 401)" in (rd / "gui" / "error").read_text(encoding="utf-8")


def test_only_light_and_dark_are_sent(tmp_path):
    out, _, rd = run(tmp_path, 'gui_theme_save auto; echo "rc=$?"; gui_theme_save "x;y"; echo "rc=$?"', None)
    assert out.splitlines() == ["rc=1", "rc=1"], out
    assert not (rd / "curl.log").exists()


def test_the_gui_record_reaches_the_save(tmp_path):
    """The `theme-dark` record goes through gui_dispatch -- after gui_role,
    because the save needs the signed-in session."""
    body = ('console_signin; token=theme-dark; gui_dispatch; echo "rc=$?"')
    out, _, rd = run(tmp_path, body, {"username": "noc", "role": "deploy", "theme": "auto"})
    assert "rc=0" in out, out
    assert json.loads((rd / "curl.body").read_text(encoding="utf-8")) == {"theme": "dark"}
