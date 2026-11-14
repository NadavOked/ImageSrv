#!/usr/bin/env python3
"""Build/verify/install metadata for ImageSrv boot payloads.

The manifest is the trust anchor: on an ISO it is copied with the media; for
updates it lives in the signed release tag.  Payload files are never installed
unless every declared input and every sha256 matches.

The inputs are everything that build_initramfs.sh takes from the source tree
(agent/, native-gui/ and the build scripts), the kernel, the build lines and
the version string stamped into the initrd as /etc/imagesrv/agent-version --
the stations compare that one with the server's version and show "the initrd
is older than the server" when they differ, so it is an input like any file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FILES = ("initrd.img", "initrd.img.gui", "vmlinuz")
INPUT_DIRS = ("agent", "native-gui")
INPUT_FILES = ("tools/build_initramfs.sh", "tools/boot-payload-build.sh",
               "tools/agent-lib-flatten.sh", "tools/initramfs-version.sh",
               "deploy/boot-payload.py")
VERSION_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")


class Refused(Exception):
    """A manifest or input that cannot be used; the message names why."""


def fail(message: str, code: int = 2) -> None:
    print(message)
    raise SystemExit(code)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def flags(path: Path) -> list[str]:
    try:
        rows = [row.strip() for row in path.read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        fail(f"cannot read flags {path}: {exc}")
    rows = [row for row in rows if row and not row.startswith("#")]
    if not rows:
        fail(f"flags file {path} has no build lines")
    return rows


def normalized_flags(rows: list[str]) -> list[str]:
    result = []
    for row in rows:
        words = row.split()
        try:
            pos = words.index("--output")
            words[pos + 1] = Path(words[pos + 1]).name
        except (ValueError, IndexError):
            fail(f"build line has no usable --output: {row}")
        result.append(" ".join(words))
    return result


def git(source: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(source), *args], capture_output=True,
                          stdin=subprocess.DEVNULL, check=False)


def stamped_version(source: Path, explicit: str = "") -> str:
    """The same string tools/initramfs-version.sh stamps into the initrd."""
    version = explicit
    why = "no version was given"
    if not version and (source / ".git").exists():
        out = git(source, "describe", "--tags", "--always")
        version = out.stdout.decode("utf-8", "replace").strip() if out.returncode == 0 else ""
        why = f"git describe exited {out.returncode}: {out.stderr.decode('utf-8', 'replace').strip()}"
    elif not version:
        manifest = Path(os.environ.get("IMAGESRV_ISO_MANIFEST", "/etc/imagesrv/iso-release.json"))
        why = f"{manifest} has no imagesrv_tag"
        try:
            tag = json.loads(manifest.read_text(encoding="utf-8")).get("imagesrv_tag")
            version = tag if isinstance(tag, str) else ""
        except (OSError, ValueError, AttributeError) as exc:
            version, why = "", f"{manifest} unreadable: {exc}"
    if not version or len(version) > 128 or not set(version) <= VERSION_CHARS:
        # C-FIL-06: "unknown" feeds the input hash and the initrd stamp -- said, on
        # stderr (stdout carries the status line the caller reads).
        print(f"boot-payload: version stamped as unknown -- {why if not version else 'not a version: ' + repr(version[:40])}",
              file=sys.stderr)
        return "unknown"
    return version


def input_files(source: Path) -> list[Path]:
    """Tracked files only when the tree is a git checkout: build artifacts
    (native-gui's binary and objects, made by `make -B` in the tree) are not
    inputs, and a fresh checkout of the same commit must hash the same."""
    fixed = [source / rel for rel in INPUT_FILES]
    missing = [str(p) for p in fixed if not p.is_file()]
    if missing:
        fail("input hash cannot be computed; missing " + ", ".join(missing))
    if (source / ".git").exists():
        out = git(source, "ls-files", "-z", "--", *INPUT_DIRS)
        if out.returncode != 0:
            fail(f"input hash cannot be computed; git ls-files failed in {source}: "
                 + out.stderr.decode("utf-8", "replace").strip())
        tree = [source / name for name in out.stdout.decode("utf-8").split("\0") if name]
    else:
        tree = [p for root in INPUT_DIRS if (source / root).is_dir()
                for p in (source / root).rglob("*") if p.is_file()]
    return fixed + sorted(tree)


def input_hash(source: Path, kernel: str, flag_rows: list[str], version: str) -> str:
    h = hashlib.sha256()
    h.update(("kernel\0" + kernel + "\0").encode())
    h.update(("version\0" + version + "\0").encode())
    for row in normalized_flags(flag_rows):
        h.update(("flag\0" + row + "\0").encode())
    for path in input_files(source):
        rel = path.relative_to(source).as_posix()
        h.update(("file\0" + rel + "\0").encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def load_manifest(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"manifest {path} is unreadable: {exc}") from exc
    required = {"kernel_version", "agent_version", "sha256", "source_commit", "flags", "input_hash"}
    if not isinstance(doc, dict) or not required <= set(doc):
        raise Refused(f"manifest {path} is missing required fields")
    if not isinstance(doc["sha256"], dict) or set(doc["sha256"]) != set(FILES) or not all(
            isinstance(doc["sha256"].get(name), str) and len(doc["sha256"][name]) == 64
            for name in FILES):
        raise Refused(f"manifest {path} does not declare exactly {', '.join(FILES)}")
    return doc


def verify_files(directory: Path, doc: dict) -> str | None:
    for name in FILES:
        path = directory / name
        if not path.is_file():
            return f"{path} is missing"
        try:
            actual = sha(path)
        except OSError as exc:
            return f"cannot hash {path}: {exc}"
        if actual != doc["sha256"][name]:
            return f"sha256 mismatch for {name}"
    return None


def create(args: argparse.Namespace) -> None:
    payload = Path(args.payload_dir)
    rows = flags(Path(args.flags))
    version = stamped_version(Path(args.source), args.version or "")
    doc = {
        "kernel_version": args.kernel,
        "agent_version": version,
        "sha256": {name: sha(payload / name) for name in FILES},
        "source_commit": args.source_commit,
        "flags": normalized_flags(rows),
        "input_hash": input_hash(Path(args.source), args.kernel, rows, version),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".new")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, out)
    print(f"manifest written: {out}")


def install_verified(source: Path, target: Path, manifest: Path, doc: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    try:
        for name in FILES:
            fd, raw = tempfile.mkstemp(prefix=name + ".", suffix=".new", dir=target)
            os.close(fd)
            temp = Path(raw)
            staged.append((temp, target / name))
            shutil.copyfile(source / name, temp)
            # codeql[py/overly-permissive-file] false positive: קובצי אתחול ציבוריים שמוגשים למחשבים ב-TFTP/HTTP (#1788)
            os.chmod(temp, 0o644)
            if sha(temp) != doc["sha256"][name]:
                fail(f"copy verification failed for {name}")
        for temp, final in staged:
            os.replace(temp, final)
        shutil.copyfile(manifest, target / "boot-payload.json.new")
        os.replace(target / "boot-payload.json.new", target / "boot-payload.json")
    finally:
        for temp, _final in staged:
            temp.unlink(missing_ok=True)


def decide(args: argparse.Namespace) -> None:
    source = Path(args.source)
    target = Path(args.payload_dir)
    rows = flags(Path(args.flags))
    version = stamped_version(source)
    if version == "unknown":
        # "Could not tell which version this tree is" is not "the same version".
        fail("rebuild\tthe source version is unknown, so no prebuilt or current payload can be matched to it", 10)
    wanted_hash = input_hash(source, args.kernel, rows, version)

    local_manifest = target / "boot-payload.json"
    if local_manifest.is_file():
        try:
            doc = load_manifest(local_manifest)
        except Refused:
            doc = None
        if doc and doc["input_hash"] == wanted_hash and not verify_files(target, doc):
            print("current\tboot payload: inputs unchanged, current payload verified")
            return

    if args.current_only:
        fail("rebuild\tcurrent boot payload inputs changed or its hashes could not be verified", 10)

    manifest = Path(args.manifest) if args.manifest else None
    prebuilt = Path(args.prebuilt_dir) if args.prebuilt_dir else None
    if manifest is None or prebuilt is None:
        fail("rebuild\tprebuilt boot payload is not available", 10)
    try:
        doc = load_manifest(manifest)
    except Refused as exc:
        fail(f"rebuild\tprebuilt manifest is invalid: {exc}", 10)
    if doc["kernel_version"] != args.kernel:
        fail(f"rebuild\tprebuilt kernel {doc['kernel_version']} does not match installed kernel {args.kernel}", 10)
    if doc["agent_version"] != version:
        fail(f"rebuild\tprebuilt payload is stamped {doc['agent_version']}, this tree is {version}", 10)
    if doc["flags"] != normalized_flags(rows):
        fail("rebuild\tprebuilt flags do not match initrd.flags", 10)
    if doc["input_hash"] != wanted_hash:
        fail("rebuild\tprebuilt inputs do not match this source tree", 10)
    bad = verify_files(prebuilt, doc)
    if bad:
        fail(f"rebuild\tprebuilt payload rejected: {bad}", 10)
    install_verified(prebuilt, target, manifest, doc)
    print("prebuilt\tboot payload: prebuilt verified and installed")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", required=True)
    common.add_argument("--kernel", required=True)
    common.add_argument("--flags", required=True)
    common.add_argument("--payload-dir", required=True)
    c = sub.add_parser("create", parents=[common])
    c.add_argument("--source-commit", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--version", help="the version passed to build_initramfs.sh --version, if any")
    d = sub.add_parser("decide", parents=[common])
    d.add_argument("--manifest")
    d.add_argument("--prebuilt-dir")
    d.add_argument("--current-only", action="store_true")
    return p


if __name__ == "__main__":
    ns = parser().parse_args()
    create(ns) if ns.command == "create" else decide(ns)
