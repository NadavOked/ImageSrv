"""#2694: consume only a complete, verified server bundle; failures name their cause."""

import json
import os
import shlex

import pytest

from native import requires_native
from server.usbstick import knowledge
from test_cloner_gui import BASH
from test_usb_clone import needs_jq, posix, run

pytestmark = [requires_native(("bash", BASH)), needs_jq]

SMART = b"# server SMART table\nata\t5\tReallocated_Sector_Ct\n"
HW = b"# server hardware table\nhw\tide\n"
ENTRY = {"model": "Test PC", "controller": None, "basis": "preflight",
         "source": "server", "mbps": 120, "samples": 2}


def json_bytes(doc):
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def bundle(tmp_path, version="v0.90.0", *, rates=None, smart=SMART, hw=HW):
    dest = tmp_path / "usbdata/server-sync"
    dest.mkdir(parents=True, exist_ok=True)
    rates = {"schema": 1, "kind": "imagesrv-rates", "entries": [dict(ENTRY)]} if rates is None else rates
    built = knowledge.Bundle(files={knowledge.RATES: json_bytes(rates), knowledge.SMART_NAME: smart,
                                    knowledge.HW_NAME: hw}, models=1,
                             smart_version=knowledge.smart_version(smart, hw))
    for name, data in built.files.items():
        (dest / name).write_bytes(data)
    (dest / "manifest.json").write_bytes(knowledge.manifest_bytes(
        built, device_id="usb-TEST", server_version=version))
    return dest


def load(tmp_path, *, agent="v0.90.0", state="mounted", same=False, setup=""):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / knowledge.SMART_NAME).write_bytes(SMART if same else b"# built-in SMART\n")
    (builtin / knowledge.HW_NAME).write_bytes(HW if same else b"# built-in hardware\n")
    # A failed load must remove even stale copies from a previous attempt.
    cached = tmp_path / "run/usb.sync.d"
    cached.mkdir(parents=True)
    (cached / "stale").write_text("must never be used\n")
    # the harness exports SMART_KB; the agent does not (smartjudge.sh), so the test must not hand the check an exported one
    out = run(tmp_path, f'unset SMART_KB; SMART_KB={shlex.quote(posix(builtin))}; '
              f'USB_DATA_STATE={shlex.quote(state)}; USB_DATA_DIR={shlex.quote(posix(tmp_path / "usbdata"))}; '
              f'AGENT_VERSION={shlex.quote(agent)}; {setup} usb_sync_load; _test_rc=$?; '
              'printf "%s\\n" "$_test_rc" "$USB_SYNC_STATE" "$USB_SYNC_TABLES" "$SMART_KB" '
              '"$USB_SYNC_NOTICE" > "$RUN_DIR/result"; '
              'sh -c \'printf "%s" "$SMART_KB"\' > "$RUN_DIR/child-kb"')
    assert out.returncode == 0, out.stdout + out.stderr
    rc, sync_state, tables, kb, notice = (tmp_path / "run/result").read_text().splitlines()
    assert rc == "0", out.stdout + out.stderr
    assert (tmp_path / "run/usb.sync").read_text().strip() == sync_state
    log = (tmp_path / "run/agent.log").read_text()
    assert "usbclone: server-sync:" in log
    return sync_state, tables, kb, notice, log


def assert_builtin(tmp_path, result, state, reason=""):
    sync_state, tables, kb, notice, log = result
    assert (sync_state, tables, kb) == (state, "builtin", posix(tmp_path / "builtin")), result
    if state == "failed":
        assert notice and reason in notice, result
        assert reason in log, result
        assert not (tmp_path / "run/usb.sync.d").exists()
    else:
        assert notice == "", result
        assert reason in log, result


def test_verified_different_tables_are_adopted_and_exported(tmp_path):
    source = bundle(tmp_path)
    result = load(tmp_path)
    state, tables, kb, notice, log = result
    dest = tmp_path / "run/usb.sync.d"
    assert (state, tables, kb, notice) == ("ok", "server", posix(dest), ""), result
    assert "server v0.90.0" in log and "stick v0.90.0" in log
    assert (tmp_path / "run/child-kb").read_text() == posix(dest)
    assert not (dest / "stale").exists()
    for name in (knowledge.SMART_NAME, knowledge.HW_NAME, knowledge.RATES, "manifest.json"):
        assert (dest / name).read_bytes() == (source / name).read_bytes()


def test_an_older_server_keeps_the_sticks_tables(tmp_path):
    bundle(tmp_path, "v0.89.0")
    assert_builtin(tmp_path, load(tmp_path), "ok", "older server (v0.89.0 < v0.90.0)")


def test_identical_tables_stay_builtin(tmp_path):
    bundle(tmp_path)
    assert_builtin(tmp_path, load(tmp_path, same=True), "ok", "same tables as built in")


@pytest.mark.parametrize("server,agent", [("", "v0.90.0"), ("v0.90.0", "unknown"),
                                          ("abc1234", "v0.90.0"), ("v1000000.0.0", "v0.90.0"),
                                          ("v0..1", "v0.90.0"), ("v0.90.x", "v0.90.0")])
def test_uncomparable_versions_fail_visibly(tmp_path, server, agent):
    bundle(tmp_path, server)
    assert_builtin(tmp_path, load(tmp_path, agent=agent), "failed", "versions")


@pytest.mark.parametrize("server,agent,tables", [
    ("v0.90.0-12-gabc1234", "v0.90.0-dirty", "server"),
    ("0.090.008", "v0.90.9", "builtin"),
    ("v1.0.0", "v0.99.999", "server"),
    ("v0.89.999", "v0.90.0", "builtin"),
])
def test_version_comparison_is_numeric_and_ignores_suffixes(tmp_path, server, agent, tables):
    bundle(tmp_path, server)
    result = load(tmp_path, agent=agent)
    assert result[:2] == ("ok", tables), result
    assert result[3] == ""
    assert server in result[4] and agent in result[4]


@pytest.mark.parametrize("state,exists", [("mounted", False), ("none", True), ("failed", True)])
def test_nothing_synced_is_not_an_error(tmp_path, state, exists):
    if exists:
        bundle(tmp_path)
    assert_builtin(tmp_path, load(tmp_path, state=state), "none", "not mounted")


def symlink(path, target, directory=False):
    try:
        os.symlink(target, path, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are not available here: {exc}")


@pytest.mark.parametrize("problem,reason", [
    ("missing_manifest", "manifest.json"), ("not_json", "manifest.json"),
    ("schema", "schema"), ("kind", "kind"), ("extra_key", "surprise"),
    ("missing_key", "written_at"), ("hash", "smart-knowledge.tsv: sha256"),
    ("size", "hw-issues.tsv: size"), ("extra_file", "extra.tsv"),
    ("mbps", "rates.json: mbps"), ("controller", "rates.json: controller"),
    ("entry_key", "rates.json: entry keys"), ("rates_schema", "rates.json: schema"),
    ("table_version", "smart_table_version"), ("directory_symlink", "server-sync is a symlink"),
    ("manifest_symlink", "manifest.json is a symlink"), ("too_large", "rates.json exceeds 1048576 bytes"),
    ("not_directory", "server-sync is not a directory"),
    ("empty_table", "smart-knowledge.tsv is empty"),
    ("multiple_json", "manifest.json"), ("missing_table", "hw-issues.tsv"),
])
def test_invalid_bundle_is_named_and_nothing_is_used(tmp_path, problem, reason):
    dest = bundle(tmp_path, smart=b"" if problem == "empty_table" else SMART)
    manifest = dest / "manifest.json"
    doc = json.loads(manifest.read_bytes())
    if problem in {"schema", "kind", "extra_key", "missing_key", "size", "extra_file", "table_version"}:
        if problem == "schema":
            doc["schema"] = 2
        elif problem == "kind":
            doc["kind"] = "other"
        elif problem == "extra_key":
            doc["surprise"] = 1
        elif problem == "missing_key":
            del doc["written_at"]
        elif problem == "size":
            doc["files"][knowledge.HW_NAME]["size"] += 1
        elif problem == "extra_file":
            doc["files"]["extra.tsv"] = doc["files"][knowledge.HW_NAME]
        else:
            doc["smart_table_version"] = "0" * 12
        manifest.write_bytes(json_bytes(doc))
    elif problem in {"mbps", "controller", "entry_key", "rates_schema"}:
        rates = json.loads((dest / knowledge.RATES).read_bytes())
        if problem == "rates_schema":
            rates["schema"] = 2
        else:
            key, value = {"mbps": ("mbps", 0), "controller": ("controller", "sata"),
                          "entry_key": ("unknown", 1)}[problem]
            rates["entries"][0][key] = value
        bundle(tmp_path, rates=rates)  # Correct hashes: exercise validation of the rates themselves.
    elif problem == "missing_manifest":
        manifest.unlink()
    elif problem == "not_json":
        manifest.write_bytes(b"{broken")
    elif problem == "hash":
        (dest / knowledge.SMART_NAME).write_bytes(b"!" + SMART[1:])
    elif problem == "directory_symlink":
        other = dest.with_name("real-sync")
        dest.rename(other)
        symlink(dest, other, directory=True)
    elif problem == "manifest_symlink":
        other = dest / "real-manifest.json"
        manifest.rename(other)
        symlink(manifest, other)
    elif problem == "too_large":
        (dest / knowledge.RATES).write_bytes(b"x" * (1048576 + 1))
    elif problem == "not_directory":
        dest.rename(dest.with_name("old-sync"))
        dest.write_bytes(b"not a directory")
    elif problem == "multiple_json":
        manifest.write_bytes(manifest.read_bytes() * 2)
    elif problem == "missing_table":
        (dest / knowledge.HW_NAME).unlink()
    assert_builtin(tmp_path, load(tmp_path), "failed", reason)


@pytest.mark.parametrize("setup,reason", [
    ("sha256sum() { return 1; };", "could not hash rates.json"),
    ("sha256sum() { return 0; };", "could not hash rates.json"),
    ("wc() { return 1; };", "manifest.json: could not read size"),
    ("wc() { printf bogus; };", "manifest.json: could not read size"),
    ("cp() { return 1; };", "manifest.json: could not copy"),
    ("mkdir() { return 1; };", "usb.sync.d: could not create directory"),
])
def test_a_check_that_could_not_run_is_not_success(tmp_path, setup, reason):
    bundle(tmp_path)
    assert_builtin(tmp_path, load(tmp_path, setup=setup), "failed", reason)
