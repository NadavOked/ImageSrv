"""#86 -- qualifying the machine during a restore: Microsoft's signed db update.

When the firmware's UEFI db lacks the CA the image's boot loader is signed by
(``boot_ca_preflight`` = failed), ``db_update_apply`` (agent/lib/postdeploy/dbupdate.sh)
appends the signed update the image carries, and reads db back.

The efivarfs here is a directory, and the firmware is the one thing mocked: a
``dd`` wrapper on PATH that, for a write to the db variable only, does what the
firmware would (``accept`` appends the update's ESL), or does nothing and
returns 0 (``ignore`` -- "the command returned 0" is not evidence), or refuses
(``reject``), or keeps only the attributes (``clobber``). Every other ``dd`` is
the real one. The certificates and the PKCS#7 signature are real (openssl): the
verification the agent runs is the verification under test.

Negative control: ``agent/lib/postdeploy/dbupdate.sh``, ``agent/lib/postdeploy/bootcadb.sh``,
``agent/lib/restore/restore.sh``, ``agent/lib/core/progress.sh`` and ``agent/imagesrv-agent``
back to the base -> see the PR body.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import uuid
from pathlib import Path

import pytest

from agentlib import agent_sources, lib_path
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
if os.name == "nt":
    for _c in (r"C:\Program Files\Git\usr\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"):
        if Path(_c).exists():
            BASH = _c
            break

pytestmark = requires_native(("bash", BASH), "openssl")

GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
DB_GUID = "d719b2cb-3d3a-4596-a3bc-dad00e67656f"
X509 = uuid.UUID("a5c059a1-94e4-4aa7-87b5-ab155c2bf072").bytes_le
PKCS7 = uuid.UUID("4aafd29d-68df-49ee-8aa9-347d375665a7").bytes_le
MS_OWNER = uuid.UUID("77fa9abd-0359-4d32-bd60-28f4e78f784b").bytes_le
ATTRS = b"\x27\0\0\0"
KEK_CA = "Microsoft Corporation KEK CA 2011"
NEW_CA = "Windows UEFI CA 2023"
OLD_CA = "Microsoft Windows Production PCA 2011"
TS = struct.pack("<HBBBBBBIhBB", 2024, 1, 15, 0, 0, 0, 0, 0, 0, 0, 0)
JQ_STUB = '#!/bin/sh\nexec python "{}" "$@"\n'


def posix(path: Path) -> str:
    text = str(path).replace("\\", "/")
    return "/" + text[0].lower() + text[2:] if len(text) > 1 and text[1] == ":" else text


def sh(script: str, env: dict | None = None, cwd: Path = REPO) -> subprocess.CompletedProcess:
    full = os.environ.copy()
    full["MSYS2_ARG_CONV_EXCL"] = "/CN=;/O="   # Git Bash: a subject "/CN=..." is not a path
    full.update(env or {})
    return subprocess.run([BASH, "-c", script], env=full, cwd=cwd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8")


def ossl(work: Path, *args: str) -> None:
    proc = sh("openssl " + " ".join(f"'{a}'" for a in args), cwd=work)
    assert proc.returncode == 0, proc.stderr


def esl(*ders: bytes) -> bytes:
    """One EFI_SIGNATURE_LIST per certificate (X.509, owner = Microsoft)."""
    out = b""
    for der in ders:
        sig = MS_OWNER + der
        out += X509 + struct.pack("<III", 28 + len(sig), 0, len(sig)) + sig
    return out


def der_len(data: bytes, at: int) -> tuple[int, int]:
    """(length, offset of the value) of the DER TLV at ``at``."""
    first = data[at + 1]
    if first < 0x80:
        return first, at + 2
    n = first & 0x7F
    return int.from_bytes(data[at + 2:at + 2 + n], "big"), at + 2 + n


def bare_signed_data(content_info: bytes) -> bytes:
    """ContentInfo { OID signedData, [0] SignedData } -> the bare SignedData UEFI also allows."""
    _, body = der_len(content_info, 0)
    oid_len, oid_at = der_len(content_info, body)
    explicit = oid_at + oid_len
    assert content_info[explicit] == 0xA0
    inner_len, inner_at = der_len(content_info, explicit)
    return content_info[inner_at:inner_at + inner_len]


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> dict:
    """Real certificates: a root, the KEK CA under it, a signer under the KEK CA; an impostor
    KEK CA with the same name and another key; and the db certificates."""
    w = tmp_path_factory.mktemp("pki")
    ec = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "3650")
    ca = ("-addext", "basicConstraints=critical,CA:TRUE")
    ossl(w, "req", "-x509", "-new", *ec, "-keyout", "root.key", "-out", "root.pem", "-subj", "/CN=Test Root", *ca)
    for name, issuer in (("kek", "root"), ("fake", None)):
        signer = ("-CA", f"{issuer}.pem", "-CAkey", f"{issuer}.key") if issuer else ()
        ossl(w, "req", "-x509", "-new", *ec, "-keyout", f"{name}.key", "-out", f"{name}.pem",
             "-subj", f"/O=Microsoft Corporation/CN={KEK_CA}", *signer, *ca)
        ossl(w, "req", "-x509", "-new", *ec, "-keyout", f"{name}-leaf.key", "-out", f"{name}-leaf.pem",
             "-subj", "/CN=Microsoft Windows UEFI Key Exchange Key", "-CA", f"{name}.pem", "-CAkey", f"{name}.key")
    for name, cn in (("old", OLD_CA), ("new", NEW_CA), ("other", "Other CA"), ("oem", "Some OEM KEK")):
        ossl(w, "req", "-x509", "-new", *ec, "-keyout", f"{name}.key", "-out", f"{name}.pem",
             "-subj", f"/O=Microsoft Corporation/CN={cn}")
    der = {}
    for name in ("kek", "fake", "old", "new", "other", "oem"):
        ossl(w, "x509", "-in", f"{name}.pem", "-outform", "DER", "-out", f"{name}.der")
        der[name] = (w / f"{name}.der").read_bytes()
    return {"dir": w, "der": der}


def payload(pki: dict, adds: str = "new", signer: str = "kek", bare: bool = False) -> tuple[bytes, bytes]:
    """(the EFI_VARIABLE_AUTHENTICATION_2 payload, the ESL a firmware would append)."""
    w = pki["dir"]
    data = esl(pki["der"][adds])
    signed = "db".encode("utf-16-le") + uuid.UUID(DB_GUID).bytes_le + struct.pack("<I", 0x67) + TS + data
    (w / "signed.bin").write_bytes(signed)
    ossl(w, "cms", "-sign", "-binary", "-in", "signed.bin", "-signer", f"{signer}-leaf.pem",
         "-inkey", f"{signer}-leaf.key", "-outform", "DER", "-out", "p7.der", "-nosmimecap", "-md", "sha256")
    p7 = (w / "p7.der").read_bytes()
    if bare:
        p7 = bare_signed_data(p7)
    cert = struct.pack("<IHH", 24 + len(p7), 0x0200, 0x0EF1) + PKCS7 + p7
    return TS + cert + data, data


DD = """#!/bin/sh
of=""; src=""
for a in "$@"; do case "$a" in of=*) of=${a#of=} ;; if=*) src=${a#if=} ;; esac; done
case "$of" in
  */db-d719b2cb-3d3a-4596-a3bc-dad00e67656f)
    echo "WRITE db" >> "$FW/log"; cp "$src" "$FW/written.bin"
    case "$(cat "$FW/mode")" in
      accept) cat "$FW/append" >> "$of"; exit 0 ;;
      ignore) exit 0 ;;
      reject) echo "dd: error writing '$of': Invalid argument" >&2; exit 1 ;;
      clobber) printf '\\047\\000\\000\\000' > "$of"; exit 0 ;;
      replace) { printf '\\047\\000\\000\\000'; cat "$FW/append"; } > "$of"; exit 0 ;;
    esac ;;
  */KEK-*|*/PK-*|*/dbx-*) echo "FORBIDDEN $of" >> "$FW/log"; exit 1 ;;
esac
exec "$REAL_DD" "$@"
"""
CHATTR = '#!/bin/sh\necho "chattr $*" >> "$FW/chattr"\nexit "$(cat "$FW/chattr_rc" 2>/dev/null || echo 0)"\n'
CURL = '#!/bin/sh\nfor a in "$@"; do u=$a; done\necho "$u" >> "$FW/urls"\n[ -f "$FW/payload" ] || exit 22\ncat "$FW/payload"\n'


def stage(tmp_path: Path, pki: dict, *, switch: object = True, setup_mode: int | None = 0,
          db_attrs: bytes = ATTRS, kek: str = "kek", fw: str = "accept", manifest_sha: str | None = None,
          no_db_update: bool = False, chattr_rc: int = 0, response: bool = True, **pay) -> dict:
    efivars = tmp_path / "efivars"
    efivars.mkdir()
    (efivars / f"SecureBoot-{GLOBAL}").write_bytes(b"\x06\0\0\0\x01")
    if setup_mode is not None:
        (efivars / f"SetupMode-{GLOBAL}").write_bytes(b"\x06\0\0\0" + bytes([setup_mode]))
    (efivars / f"db-{DB_GUID}").write_bytes(db_attrs + esl(pki["der"]["old"]))
    (efivars / f"KEK-{GLOBAL}").write_bytes(ATTRS + esl(pki["der"][kek]))
    (efivars / f"PK-{GLOBAL}").write_bytes(ATTRS + b"PK-UNTOUCHED" * 4)
    (efivars / f"dbx-{DB_GUID}").write_bytes(ATTRS + b"DBX-UNTOUCHED" * 4)
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "bootca-required.txt").write_text(NEW_CA + "\n", encoding="utf-8", newline="\n")
    if response:
        body = {} if switch is None else {"secure_boot_db_update": switch}
        (run / "response.json").write_text(json.dumps(body), encoding="utf-8")
    fwd = tmp_path / "fw"
    fwd.mkdir()
    blob, appended = payload(pki, **pay)
    (fwd / "payload").write_bytes(blob)
    (fwd / "append").write_bytes(appended)
    (fwd / "mode").write_text(fw, encoding="utf-8")
    (fwd / "chattr_rc").write_text(str(chattr_rc), encoding="utf-8")
    manifest = {"boot_ca": [NEW_CA]}
    if not no_db_update:
        manifest["db_update"] = {"file": "db-update.bin",
                                 "sha256": manifest_sha or hashlib.sha256(blob).hexdigest()}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    stubs = {"dd": DD, "chattr": CHATTR, "curl": CURL}
    if shutil.which("jq") is None:   # Windows: the narrow shim, only when there is no real jq
        stubs["jq"] = JQ_STUB.format(posix(Path(__file__).with_name("jq_shim.py")))
    for name, text in stubs.items():
        (tools / name).write_text(text, encoding="utf-8", newline="\n")
        (tools / name).chmod(0o755)
    before = {p.name: p.read_bytes() for p in efivars.iterdir()}
    return {"efivars": efivars, "run": run, "fw": fwd, "tools": tools, "blob": blob,
            "appended": appended, "before": before, "manifest": tmp_path / "manifest.json"}


def apply(s: dict, call: str = "") -> dict:
    lib = REPO / "agent" / "lib"
    script = (f'REAL_DD=$(command -v dd); export REAL_DD; export PATH={posix(s["tools"])!r}:"$PATH"; '
              f'. {posix(lib / "core/common.sh")!r}; . {posix(lib / "postdeploy/bootcadb.sh")!r}; '
              f'. {posix(lib / "postdeploy/dbupdate.sh")!r}; '
              + (call or f'db_update_apply {posix(s["manifest"])!r} sda http://srv:7712 img_1; echo "rc=$?"'))
    proc = sh(script, env={"EFIVARS_ROOT": posix(s["efivars"]), "RUN_DIR": posix(s["run"]),
                           "FW": posix(s["fw"])})
    report = s["run"] / "targets" / "sda" / "db_update.json"
    fw_log = s["fw"] / "log"
    return {"proc": proc, "out": proc.stdout,
            "report": json.loads(report.read_text(encoding="utf-8")) if report.exists() else None,
            "fw_log": fw_log.read_text(encoding="utf-8") if fw_log.exists() else "",
            "log": (s["run"] / "agent.log").read_text(encoding="utf-8") if (s["run"] / "agent.log").exists() else "",
            "after": {p.name: p.read_bytes() for p in s["efivars"].iterdir()}}


def assert_untouched(s: dict, r: dict, chattr_tried: bool = False) -> None:
    """Nothing reached the firmware: no write, every variable byte for byte as it was."""
    assert r["fw_log"] == "", r["fw_log"]
    assert r["after"] == s["before"]
    flags = (s["fw"] / "chattr").read_text(encoding="utf-8") if (s["fw"] / "chattr").exists() else ""
    assert flags.count("chattr ") == (1 if chattr_tried else 0), flags   # a failed -i is the only one


def assert_never_kek_pk_dbx(s: dict, r: dict) -> None:
    for name in (f"KEK-{GLOBAL}", f"PK-{GLOBAL}", f"dbx-{DB_GUID}", f"SetupMode-{GLOBAL}"):
        if name in s["before"]:
            assert r["after"][name] == s["before"][name], name
    assert "FORBIDDEN" not in r["fw_log"]


# --- the write that is allowed --------------------------------------------------------------------


@pytest.mark.parametrize("bare", [False, True], ids=["content-info", "bare-signed-data"])
def test_a_signed_update_is_appended_and_the_ca_is_read_back(tmp_path, pki, bare):
    s = stage(tmp_path, pki, bare=bare)
    r = apply(s)
    assert "rc=0" in r["out"], r["out"] + r["proc"].stderr + r["log"]
    assert r["report"]["state"] == "applied", r["report"]
    assert r["report"]["before"] == [OLD_CA]
    assert r["report"]["after"] == [OLD_CA, NEW_CA]
    # exactly one write, append (0x67), and the payload byte for byte as Microsoft signed it
    assert r["fw_log"] == "WRITE db\n"
    assert (s["fw"] / "written.bin").read_bytes() == b"\x67\0\0\0" + s["blob"]
    chattr = (s["fw"] / "chattr").read_text(encoding="utf-8").splitlines()
    assert chattr[0].startswith("chattr -i ") and chattr[0].endswith(f"db-{DB_GUID}")
    assert chattr[1].startswith("chattr +i ")
    assert_never_kek_pk_dbx(s, r)
    assert (s["run"] / "targets/sda/boot_ca_state").read_text(encoding="utf-8") == "ok"
    assert (s["fw"] / "urls").read_text(encoding="utf-8").strip() == \
        "http://srv:7712/api/v1/images/img_1/files/db-update.bin"
    assert "db update: applied" in r["log"] and OLD_CA in r["log"] and NEW_CA in r["log"]


# --- every refusal: nothing is written -------------------------------------------------------------


REFUSALS = {
    "switch-off": ({"switch": False}, "כבוי בהגדרות"),
    "switch-unsaid": ({"switch": None}, "כבוי בהגדרות"),
    "switch-not-a-bool": ({"switch": "true"}, "כבוי בהגדרות"),
    "no-hello-answer": ({"response": False}, "כבוי בהגדרות"),
    "no-update-in-image": ({"no_db_update": True}, "אין עדכון db"),
    "setup-mode-1": ({"setup_mode": 1}, "SetupMode"),
    "setup-mode-unread": ({"setup_mode": None}, "SetupMode"),
    "db-attrs-not-0x27": ({"db_attrs": b"\x07\0\0\0"}, "0x27"),
    "kek-without-microsoft": ({"kek": "oem"}, "ב-KEK אין Microsoft Corporation KEK CA 2011"),
    "sha-mismatch": ({"manifest_sha": "0" * 64}, "sha256"),
    "impostor-signature": ({"signer": "fake"}, "החתימה"),
    "update-without-the-ca": ({"adds": "other"}, "אינו מוסיף"),
    "chattr-fails": ({"chattr_rc": 1}, "chattr"),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_an_unclear_state_writes_nothing(tmp_path, pki, case):
    kwargs, why = REFUSALS[case]
    if case == "switch-not-a-bool" and shutil.which("jq") is None:
        pytest.skip('the jq shim prints a string raw; only the real jq tells "true" from true')
    s = stage(tmp_path, pki, **kwargs)
    r = apply(s)
    assert "rc=1" in r["out"], r["out"] + r["proc"].stderr + r["log"]
    assert r["report"]["state"] == "refused", r["report"]
    assert why in r["report"]["reason"], r["report"]
    assert_untouched(s, r, chattr_tried=case == "chattr-fails")
    assert (s["run"] / "targets/sda/boot_ca_state").exists() is False   # the preflight's verdict is not touched
    assert "db update: refused" in r["log"]


# --- a write that did not take: failed, by name ---------------------------------------------------


@pytest.mark.parametrize("mode", ["ignore", "reject", "clobber", "replace"])
def test_a_write_whose_read_back_lacks_the_ca_fails(tmp_path, pki, mode):
    """`replace`: the CA is there, and the certificate db held before is gone -- also a failure."""
    s = stage(tmp_path, pki, fw=mode)
    r = apply(s)
    assert "rc=1" in r["out"], r["out"] + r["proc"].stderr + r["log"]
    assert r["report"]["state"] == "failed", r["report"]
    assert r["fw_log"] == "WRITE db\n"
    assert r["report"]["before"] == [OLD_CA]
    if mode == "replace":
        assert r["report"]["after"] == [NEW_CA] and "חסרה תעודה שהייתה בו" in r["report"]["reason"], r["report"]
    else:
        assert NEW_CA not in r["report"]["after"]
    assert_never_kek_pk_dbx(s, r)
    assert "not evidence; reading db back" in r["log"]
    assert not (s["run"] / "targets/sda/boot_ca_state").exists()


# --- the report -------------------------------------------------------------------------------------


def test_the_progress_report_carries_db_before_and_after(tmp_path, pki):
    s = stage(tmp_path, pki)
    for name, value in (("state", "waiting"), ("base", "0"), ("bytes.raw", ""), ("total", "100")):
        (s["run"] / "targets" / "sda" / name).write_text(value, encoding="utf-8")
    r = apply(s, 'db_update_apply ' + repr(posix(s["manifest"])) + ' sda http://srv img_1 >/dev/null; '
              f'. {posix(REPO / "agent/lib/core/progress.sh")!r}; '
              'build_progress session-1 aa:bb:cc:dd:ee:ff')
    assert r["proc"].returncode == 0, r["proc"].stderr
    target = json.loads(r["out"].strip().splitlines()[-1])["targets"][0]
    assert target["db_update"]["state"] == "applied"
    assert target["db_update"]["after"] == [OLD_CA, NEW_CA]
    assert target["boot_ca_preflight"]["state"] == "ok"


# --- run_restore: the gate before the table ---------------------------------------------------------


@requires_native("jq")
@pytest.mark.parametrize("switch, reaches_table", [(True, True), (False, False)])
def test_run_restore_writes_the_table_only_once_db_holds_the_ca(tmp_path, pki, switch, reaches_table):
    s = stage(tmp_path, pki, switch=switch)
    sources = agent_sources("common", "sysinfo", "failmark", "progress", "bootcadb", "dbupdate", "restore")
    call = (sources +
            "disk_fits() { return 0; }; write_check() { return 0; }; write_speed_verdict() { return 0; }; "
            "readback_drawers() { :; }; plan_gate() { return 0; }; fail_written_target() { :; }; "
            "apply_gpt() { echo APPLY_GPT; return 1; }; "
            f'run_restore multicast sda http://srv img_1 {posix(s["manifest"])!r}; echo "rc=$?"')
    r = apply(s, call)
    assert ("APPLY_GPT" in r["out"]) is reaches_table, r["out"] + r["proc"].stderr + r["log"]
    target = s["run"] / "targets" / "sda"
    if not reaches_table:
        assert (target / "error").read_text(encoding="utf-8").strip() == \
            "המחשב לא יאמין לאימג' — Secure Boot יחסום אתחול"
        assert_untouched(s, r)
    else:
        assert r["report"]["state"] == "applied"


def test_the_agent_sources_the_library_and_the_build_packs_chattr():
    main = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/bootcadb.sh"; . "$LIB_DIR/dbupdate.sh"; ' in main
    assert lib_path("dbupdate.sh").parent.name == "postdeploy"
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert " chattr)" in builder and "kernel/fs/efivarfs" in builder


def test_dbupdate_writes_db_only():
    """Static guard beside the behaviour: the one `of=` in the library is the db variable."""
    text = (REPO / "agent" / "lib" / "postdeploy" / "dbupdate.sh").read_text(encoding="utf-8")
    writes = [line for line in text.splitlines() if "of=" in line and not line.lstrip().startswith("#")]
    assert all('of="$1/' in w or 'of="$_du_db"' in w for w in writes), writes
    assert 'of="$_du_db"' in "".join(writes)
    assert '_du_db="$EFIVARS_ROOT/db-$EFI_DB_GUID"' in text
    for forbidden in ("dbx-", "KEK-$EFI_GLOBAL_GUID\" ", "PK-", "SetupMode-$EFI_GLOBAL_GUID\" bs"):
        assert not any(forbidden in w for w in writes)
