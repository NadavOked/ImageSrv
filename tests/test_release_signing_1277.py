"""‏#1277 — כפתור העדכון מסרב לגרסה שאינה חתומה במפתח שהשרת סומך עליו.

חתימות **אמיתיות**: מפתחות זמניים מ-``ssh-keygen`` ותגים מ-``git tag -s``,
כמו בפרסום עצמו. המאמת (``tools/verify-release-tag.py``) הוא מימוש עצמאי
של הפורמט — ולכן כל מקרה חיובי נבדק גם מול ``git verify-tag``: אם שניהם
מסכימים, הטסט לא בדק את המאמת מול עצמו.

ארבעה מקרים נדרשים: חתום עובר · לא חתום נדחה · חתום במפתח אחר נדחה ·
אימות שלא רץ = כישלון, לא מעבר.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from native import requires_native

ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "tools" / "verify-release-tag.py"
SIGNERS = ROOT / "install" / "release-signers"
KEYGEN = shutil.which("ssh-keygen")

pytestmark = requires_native("ssh-keygen", "git", why="חתימות אמיתיות של git tag -s")

PRINCIPAL = "imagectl-release"


def _run(cmd) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                          timeout=60)


def git(repo: Path, *args: str) -> str:
    done = _run(["git", "-C", repo.as_posix(), "-c", "user.name=t", "-c", "user.email=t@t",
                 "-c", "gpg.format=ssh", "-c", "tag.gpgSign=false", *args])
    assert done.returncode == 0, done.stderr
    return done.stdout


def make_key(tmp: Path, name: str, kind: str = "ed25519") -> Path:
    key = tmp / name
    extra = ["-b", "3072"] if kind == "rsa" else []
    done = _run([KEYGEN, "-q", "-t", kind, *extra, "-N", "", "-C", name, "-f", key.as_posix()])
    assert done.returncode == 0, done.stderr
    return key


def signers_file(tmp: Path, *keys: Path, name: str = "signers") -> Path:
    lines = ["# test\n"]
    for key in keys:
        kind, b64 = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
        lines.append(f'{PRINCIPAL} namespaces="git" {kind} {b64} {key.name}\n')
    path = tmp / name
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return path


def key_fingerprint(key: Path) -> str:
    out = _run([KEYGEN, "-l", "-f", key.with_suffix(".pub").as_posix()]).stdout
    return out.split()[1]


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "release")
    return repo


def sign(repo: Path, tag: str, key: Path) -> None:
    git(repo, "-c", f"user.signingkey={key.as_posix()}", "tag", "-s", "-m", f"ImageCtl {tag}", tag)


def verify(repo: Path, tag: str, signers: Path) -> subprocess.CompletedProcess:
    return _run([sys.executable, "-I", VERIFY, repo.as_posix(), tag, signers.as_posix()])


def load_verifier():
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_release_tag", VERIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git_verify_tag(repo: Path, tag: str, signers: Path) -> int:
    return _run(["git", "-C", repo.as_posix(), "-c", f"gpg.ssh.allowedSignersFile={signers.as_posix()}",
                 "verify-tag", tag]).returncode


@pytest.fixture()
def world(tmp_path: Path) -> dict:
    release, other = make_key(tmp_path, "release"), make_key(tmp_path, "other")
    return {"repo": make_repo(tmp_path), "release": release, "other": other,
            "signers": signers_file(tmp_path, release), "tmp": tmp_path}


# --- המאמת --------------------------------------------------------------------


def test_a_tag_signed_by_the_release_key_passes_with_its_fingerprint(world):
    repo, signers = world["repo"], world["signers"]
    sign(repo, "v1.0.0", world["release"])
    done = verify(repo, "v1.0.0", signers)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"signed: {PRINCIPAL} {key_fingerprint(world['release'])}"
    assert git_verify_tag(repo, "v1.0.0", signers) == 0       # git מסכים — חתימה אמיתית


def test_an_rsa_release_key_is_accepted_too(world):
    """אם FIPS במארח חוסם ed25519 — RSA (‏rsa-sha2-512) עובד באותו מסלול."""
    rsa = make_key(world["tmp"], "release-rsa", "rsa")
    signers = signers_file(world["tmp"], rsa, name="signers-rsa")
    sign(world["repo"], "v1.0.0", rsa)
    done = verify(world["repo"], "v1.0.0", signers)
    assert done.returncode == 0, done.stderr
    assert git_verify_tag(world["repo"], "v1.0.0", signers) == 0


def test_an_annotated_unsigned_tag_is_refused(world):
    git(world["repo"], "tag", "-a", "-m", "plain", "v1.0.0")
    done = verify(world["repo"], "v1.0.0", world["signers"])
    assert done.returncode == 1 and done.stderr.startswith("unsigned:"), done.stderr
    assert "אינו חתום" in done.stderr


def test_a_lightweight_tag_is_refused(world):
    git(world["repo"], "tag", "v1.0.0")
    done = verify(world["repo"], "v1.0.0", world["signers"])
    assert done.returncode == 1 and "תג קל" in done.stderr, done.stderr


def test_a_tag_signed_by_another_key_is_refused_and_names_that_key(world):
    sign(world["repo"], "v1.0.0", world["other"])
    done = verify(world["repo"], "v1.0.0", world["signers"])
    assert done.returncode == 1, done.stderr
    assert key_fingerprint(world["other"]) in done.stderr
    assert git_verify_tag(world["repo"], "v1.0.0", world["signers"]) != 0   # וגם git מסרב


def test_a_tag_edited_after_signing_is_refused(world):
    """אותה חתימה, הודעה אחרת — האימות הקריפטוגרפי עצמו נבדק, לא רק המפתח."""
    repo = world["repo"]
    sign(repo, "v1.0.0", world["release"])
    raw = git(repo, "cat-file", "tag", "v1.0.0").replace("ImageCtl v1.0.0", "ImageCtl v1.0.0 evil")
    path = world["tmp"] / "forged-tag"
    path.write_bytes(raw.encode("utf-8"))                  # בייטים: בלי \r\n של ווינדוס
    forged = git(repo, "hash-object", "-t", "tag", "-w", path.as_posix()).strip()
    git(repo, "update-ref", "refs/tags/v1.0.0", forged)
    done = verify(repo, "v1.0.0", world["signers"])
    assert done.returncode == 1 and "אינה תקפה" in done.stderr, done.stderr
    assert git_verify_tag(repo, "v1.0.0", world["signers"]) != 0


def test_a_signed_old_release_under_a_new_name_is_refused(world):
    """הפניה ‏refs/tags/v9.9.9 לאובייקט התג החתום של v1.0.0 — החתימה תקפה,
    אבל היא על v1.0.0. ‏git verify-tag לבדו היה מקבל את זה."""
    repo = world["repo"]
    sign(repo, "v1.0.0", world["release"])
    git(repo, "update-ref", "refs/tags/v9.9.9", git(repo, "rev-parse", "refs/tags/v1.0.0").strip())
    done = verify(repo, "v9.9.9", world["signers"])
    assert done.returncode == 1 and "שאינו תואם" in done.stderr, done.stderr
    assert git_verify_tag(repo, "v9.9.9", world["signers"]) == 0     # git לבדו — עובר


# --- "לא נבדק" — קוד 2, ואף פעם לא 0 ---------------------------------------------


def test_a_missing_signers_file_is_unchecked_not_signed(world):
    sign(world["repo"], "v1.0.0", world["release"])
    done = verify(world["repo"], "v1.0.0", world["tmp"] / "nope")
    assert done.returncode == 2 and done.stderr.startswith("unchecked:"), done.stderr


def test_a_signers_file_without_a_key_is_unchecked_not_open(world):
    sign(world["repo"], "v1.0.0", world["release"])
    empty = world["tmp"] / "empty"
    empty.write_text("# nothing yet\n\n", encoding="utf-8")
    done = verify(world["repo"], "v1.0.0", empty)
    assert done.returncode == 2 and "אין אף מפתח" in done.stderr, done.stderr


def test_an_unsupported_signer_option_is_unchecked(world):
    sign(world["repo"], "v1.0.0", world["release"])
    line = world["signers"].read_text(encoding="utf-8").replace('namespaces="git"', "cert-authority")
    bad = world["tmp"] / "bad"
    bad.write_text(line, encoding="utf-8")
    assert verify(world["repo"], "v1.0.0", bad).returncode == 2


def test_a_tag_that_is_not_there_is_unchecked(world):
    done = verify(world["repo"], "v1.0.0", world["signers"])
    assert done.returncode == 2 and "לא נמצא" in done.stderr, done.stderr


def test_the_shipped_signers_file_is_well_formed():
    """כל שורה שאינה הערה היא מפתח שהמאמת קורא. קובץ בלי מפתח עדיין חוקי
    כאן — הפרסום (release-sign.sh) ובניית ה-ISO הם שמסרבים לו."""
    mod = load_verifier()
    try:
        keys = mod.load_signers(str(SIGNERS))
    except mod.Unchecked as exc:
        assert "אין אף מפתח" in str(exc), exc
    else:
        assert all(principal and blob for principal, blob in keys)


# --- בדיקת-העדכון בקונסולה: ההוק האמיתי מול "ציבורי" מקומי ---------------------------


def _installed_tree(tmp: Path, signers: Path) -> Path:
    tree = tmp / "installed"
    (tree / "tools").mkdir(parents=True)
    (tree / "install").mkdir()
    shutil.copy(VERIFY, tree / "tools" / "verify-release-tag.py")
    shutil.copy(signers, tree / "install" / "release-signers")
    return tree


def test_the_console_check_reads_signed_unsigned_and_unreachable_in_name(world):
    from server import update
    repo, tree = world["repo"], _installed_tree(world["tmp"], world["signers"])
    sign(repo, "v0.72.0", world["release"])
    sign(repo, "v0.73.0", world["other"])
    url = repo.as_posix()
    ok = update._verify_remote_tag(url, "v0.72.0", tree)
    assert ok == {"state": "signed",
                  "detail": f"{PRINCIPAL} {key_fingerprint(world['release'])}"}, ok
    bad = update._verify_remote_tag(url, "v0.73.0", tree)
    assert bad["state"] == "unsigned" and key_fingerprint(world["other"]) in bad["detail"], bad
    gone = update._verify_remote_tag((world["tmp"] / "no-such-repo").as_posix(), "v0.72.0", tree)
    assert gone["state"] == "unchecked" and "הורדת התג" in gone["detail"], gone
    assert git(repo, "status", "--porcelain") == ""        # הבדיקה לא כתבה כלום לריפו


# --- השער: tools/server-upgrade.sh מסרב לפני ה-checkout ----------------------------
#
# הסקריפט האמיתי, מול עץ שהוא ריפו git אמיתי עם תגים חתומים אמיתיים, והמאמת
# האמיתי מועתק לתוכו. ה-git שעל ה-PATH מזויף ל-fetch/checkout/describe (בלי
# רשת ובלי לגעת בעץ), ומעביר את `cat-file` — מה שהמאמת קורא — ל-git האמיתי.

from test_installer_1131 import (  # noqa: E402
    BASH, _fake_repo, _seed_db, _status, _upgrade_env, calls, fake_tool, posix, run_bash)

requires_bash = requires_native(("bash", BASH), why="server-upgrade.sh הוא bash")
REAL_GIT = shutil.which("git")


def _git_shim(bindir: Path) -> None:
    real = Path(REAL_GIT).as_posix() if REAL_GIT else "git-missing"
    fake_tool(bindir, "git", f'''\
case " $* " in *" cat-file "*) exec "{real}" "$@" ;; esac
if [ "$1" = -C ]; then shift 2; fi
case "$1" in
  fetch|checkout) exit 0 ;;
  describe) echo "$FAKE_TAG" ;;
  rev-parse) echo "commit-of-$FAKE_TAG" ;;
  rev-list) echo "commit-of-$4" ;;
  *) echo "unexpected git $*" >&2; exit 64 ;;
esac''')
    fake_tool(bindir, "systemctl",
              'case "$1" in show) echo "IMAGECTL_URL=http://127.0.0.1:8080" ;; esac; exit 0')
    fake_tool(bindir, "curl", "exit 0")


@pytest.fixture()
def server(tmp_path: Path, world) -> dict:
    """עץ מותקן: ריפו git עם המאמת האמיתי ו-release-signers של מפתח השחרור."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    shutil.copy(VERIFY, repo / "tools" / "verify-release-tag.py")
    shutil.copy(world["signers"], repo / "install" / "release-signers")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "installed tree")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _git_shim(bindir)
    (tmp_path / "units").mkdir()
    return {"data": data, "repo": repo, "bindir": bindir, "tmp": tmp_path}


def _upgrade(server: dict, tag: str = "v0.72.0"):
    env = {**_upgrade_env(server["tmp"], server["repo"], server["data"]), "FAKE_TAG": tag}
    repo = server["repo"]
    return run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
                    server["tmp"], server["bindir"], env=env)


def _refused(server: dict, proc, *needles: str) -> dict:
    status = _status(server["data"])
    log = calls(server["tmp"])
    assert proc.returncode != 0, (server["tmp"] / "upgrade.log").read_text(encoding="utf-8")
    assert status and status["state"] == "failed" and status["tag"] == "v0.72.0", status
    assert "לא אומת כחתום" in status["error"], status["error"]
    for needle in needles:
        assert needle in status["error"], status["error"]
    assert not any(" checkout " in row for row in log), log      # העץ לא הוחלף
    assert not any(row.startswith("systemctl ") for row in log), log
    return status


@requires_bash
def test_upgrade_to_a_signed_tag_verifies_before_checkout(server, world):
    sign(server["repo"], "v0.72.0", world["release"])
    proc = _upgrade(server)
    log = calls(server["tmp"])
    text = (server["tmp"] / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, text
    assert f"חתימה: v0.72.0 signed: {PRINCIPAL} {key_fingerprint(world['release'])}" in text
    fetch = next(i for i, row in enumerate(log) if row.endswith(
        " fetch --no-tags origin refs/tags/v0.72.0:refs/tags/v0.72.0"))
    checkout = next(i for i, row in enumerate(log) if " checkout --detach v0.72.0" in row)
    assert fetch < checkout
    # בלינוקס המאמת עובר דרך ה-shim, ואז גם הסדר שלו נראה ביומן
    checked = [i for i, row in enumerate(log) if " cat-file tag refs/tags/v0.72.0" in row]
    assert all(fetch < i < checkout for i in checked), log


@requires_bash
def test_upgrade_to_an_unsigned_tag_is_refused_in_name(server):
    git(server["repo"], "tag", "-a", "-m", "plain", "v0.72.0")
    _refused(server, _upgrade(server), "unsigned:")


@requires_bash
def test_upgrade_to_a_tag_signed_by_another_key_is_refused(server, world):
    sign(server["repo"], "v0.72.0", world["other"])
    _refused(server, _upgrade(server), "unsigned:", key_fingerprint(world["other"]))


@requires_bash
def test_an_installed_tree_without_a_release_key_refuses_every_update(server, world):
    """המעבר (fail-closed): שרת שהעץ שלו בלי מפתח — לא "פתוח עד שיגיע מפתח"."""
    (server["repo"] / "install" / "release-signers").write_text("# no key\n", encoding="utf-8")
    sign(server["repo"], "v0.72.0", world["release"])
    _refused(server, _upgrade(server), "unchecked:", "אין אף מפתח")


@requires_bash
def test_a_verifier_that_could_not_run_is_a_refusal(server, world):
    sign(server["repo"], "v0.72.0", world["release"])
    (server["repo"] / "tools" / "verify-release-tag.py").unlink()
    _refused(server, _upgrade(server), "verify-release-tag.py")


@requires_bash
def test_a_verifier_that_exits_zero_without_evidence_is_a_refusal(server, world):
    """אימות ש"עבר" בלי לומר מה אומת — שקט אינו ראיה (עיקרון 5)."""
    sign(server["repo"], "v0.72.0", world["release"])
    (server["repo"] / "tools" / "verify-release-tag.py").write_text("pass\n", encoding="utf-8")
    _refused(server, _upgrade(server), "המאמת לא החזיר דבר")


# --- ה-ISO: עץ בלי מפתח לא נארז (שרת ממנו היה מסרב לכל עדכון) ---------------------

BUILD_ISO = ROOT / "tools" / "iso" / "build-iso.sh"


def _iso_signer_check() -> str:
    text = BUILD_ISO.read_text(encoding="utf-8")
    start = text.index("# ‏#1277:")
    end = text.index("\n", text.index('say "release-signers:'))
    return text[start:end + 1]


@requires_bash
@pytest.mark.parametrize("content, ok", [
    (None, False),                                          # הקובץ חסר
    ("# only comments\n\n", False),                         # בלי מפתח
    ('imagectl-release namespaces="git" ssh-ed25519 AAAA', True),   # בלי שורה חדשה בסוף — עדיין מפתח
])
def test_build_iso_refuses_a_tree_without_a_release_key(tmp_path, content, ok):
    tree = tmp_path / "iso" / "imagectl-src" / "install"
    tree.mkdir(parents=True)
    if content is not None:
        (tree / "release-signers").write_text(content, encoding="utf-8", newline="\n")
    script = ('set -euo pipefail\ndie() { echo "DIE $*" >&2; exit 1; }\nsay() { echo "SAY $*"; }\n'
              f'ISO_TREE="{posix(tmp_path / "iso")}"; TAG=v0.72.0\n' + _iso_signer_check())
    proc = run_bash(script, tmp_path, tmp_path)
    assert (proc.returncode == 0) is ok, proc.stdout + proc.stderr
    if ok:
        assert "SAY release-signers: 1" in proc.stdout
    else:
        assert "DIE" in proc.stderr and "release-signers" in proc.stderr, proc.stderr


# הפרסום (publish-to-public.sh חותם בכל פרסום) נבדק ב-tests/test_publish_to_public.py
# — הכלי והטסט שלו פרטיים, ולכן הם לא כאן.
