"""#2761: the live installer finds its medium also on a FAT32 stick.

נדב, 09/11, ברזל: דיסק-און-קי שנצרב ב-Rufus **במצב ברירת המחדל** (העתקת
קבצים, FAT32) עלה למסך "מדיית ההתקנה לא נמצאה". השורש: תווית FAT היא עד
11 תווים ו-Rufus חותך ומעלה לאותיות גדולות, כלומר `IMAGESRV_IN` ולא
`IMAGESRV_INSTALL` (16) -- ולכן `blkid -L` לא מצא דבר. ובנוסף `vfat` לא
נטען בנתיב העיגון, ו-`mount` רץ בלי `-t`.

הטסט מריץ את `installer_mount_media` האמיתי ב-bash. גבול המערכת (blkid,
modprobe, mount) מזויף כפונקציות: `mount` מצליח על vfat רק אם `vfat` נטען
וה-`-t` שמבקשים חייב להתאים לסוג ההתקן -- כמו הקרנל בלי המודול.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_agent import BASH, REPO, posix

BOOT = REPO / "agent" / "lib" / "core" / "installer_boot.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש")


def _mount_media(tmp_path: Path, devices: dict[str, tuple[str, str, bool]]) -> dict:
    """devices: dev -> (label, fs type, carries imagesrv-iso.json). Returns rc, notes, mounts."""
    t = tmp_path
    blk = "".join(f'{dev}|{label}|{fs}|{int(marker)}\n' for dev, (label, fs, marker) in devices.items())
    (t / "devs").write_text(blk, encoding="utf-8", newline="\n")
    harness = t / "harness.sh"
    harness.write_text(f"""
CDROM={posix(t)}/cdrom INSTALL_MEDIA_FILE={posix(t)}/install-media
DEVS={posix(t)}/devs; LOG={posix(t)}/calls; : > "$LOG"
. {posix(BOOT)}
sleep() {{ :; }}
modprobe() {{ echo "modprobe $1" >> "$LOG"; [ "$1" = vfat ] && : > {posix(t)}/vfat-loaded; return 0; }}
blkid() {{
    if [ "$1" = -L ]; then
        while IFS='|' read -r d l f m; do [ "$l" = "$2" ] && {{ echo "$d"; return 0; }}; done < "$DEVS"
        return 2
    fi
    while IFS='|' read -r d l f m; do
        [ "$d" = "$1" ] && {{ echo "$d: LABEL=\\"$l\\" UUID=\\"1234\\" TYPE=\\"$f\\""; return 0; }}
    done < "$DEVS"
    return 2
}}
mount() {{
    echo "mount $*" >> "$LOG"
    want=; prev=; dev=
    for a in "$@"; do [ "$prev" = -t ] && want=$a; case "$a" in /dev/*) dev=$a ;; esac; prev=$a; done
    fs=$(grep "^$dev|" "$DEVS" | cut -d'|' -f3)
    [ -z "$want" ] || [ "$want" = "$fs" ] || return 32
    case "$fs" in
        vfat) [ -e {posix(t)}/vfat-loaded ] || return 32 ;;   # a filesystem that is not registered cannot be tried
        iso9660) ;;
        *) return 32 ;;
    esac
    grep "^$dev|" "$DEVS" | grep -q '|1$' && : > "$CDROM/imagesrv-iso.json"
    return 0
}}
installer_mount_media; echo "rc=$?"
""", encoding="utf-8", newline="\n")
    got = subprocess.run([BASH, posix(harness)], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL)
    calls = (t / "calls").read_text(encoding="utf-8").splitlines()
    media = t / "install-media"
    return {"out": got.stdout + got.stderr, "calls": calls,
            "media": media.read_text(encoding="utf-8").strip() if media.exists() else None,
            "mounts": [c for c in calls if c.startswith("mount ")]}


def test_fat32_stick_with_the_truncated_label_is_mounted_as_vfat(tmp_path):
    """Rufus copy mode: label IMAGESRV_IN, vfat, the files of the ISO on it."""
    r = _mount_media(tmp_path, {"/dev/sdb1": ("IMAGESRV_IN", "vfat", True)})
    assert "rc=0" in r["out"], r["out"]
    assert r["media"] == "/dev/sdb1"
    assert "modprobe vfat" in r["calls"]
    assert len(r["mounts"]) == 1 and r["mounts"][0].startswith("mount -t vfat -o ro /dev/sdb1 "), r["mounts"]
    assert "media /dev/sdb1 (vfat) mounted" in r["out"]


def test_dd_written_iso_stick_still_mounts_as_iso9660(tmp_path):
    """The byte-copy stick (Rufus DD Image) -- the path that worked before; guard against a fix that breaks it."""
    r = _mount_media(tmp_path, {"/dev/sdb": ("IMAGESRV_INSTALL", "iso9660", True)})
    assert "rc=0" in r["out"], r["out"]
    assert r["media"] == "/dev/sdb"
    assert len(r["mounts"]) == 1 and "-t iso9660" in r["mounts"][0], r["mounts"]


def test_an_unrelated_fat_stick_is_never_mounted(tmp_path):
    """The label is a filter, not a licence: a stick named KINGSTON is left alone."""
    r = _mount_media(tmp_path, {"/dev/sdb1": ("KINGSTON", "vfat", True)})
    assert "rc=1" in r["out"], r["out"]
    assert r["mounts"] == [] and r["media"] is None
    assert "no media with label IMAGESRV_INSTALL (iso9660) or IMAGESRV_IN (vfat)" in r["out"]


def test_a_stick_with_the_label_but_not_vfat_is_not_taken_as_fat_media(tmp_path):
    """The short label is only evidence together with the vfat type."""
    r = _mount_media(tmp_path, {"/dev/sdb1": ("IMAGESRV_IN", "ext4", True)})
    assert "rc=1" in r["out"], r["out"]
    assert r["mounts"] == []


def test_a_fat_stick_without_the_manifest_is_named_not_accepted(tmp_path):
    """Principle 5: the proof is imagesrv-iso.json, not the label."""
    r = _mount_media(tmp_path, {"/dev/sdb1": ("IMAGESRV_IN", "vfat", False)})
    assert "rc=1" in r["out"], r["out"]
    assert r["media"] is None
    assert "/dev/sdb1 (vfat) is not an ImageSrv install medium (no imagesrv-iso.json)" in r["out"]


def test_no_device_at_all_is_a_named_failure(tmp_path):
    r = _mount_media(tmp_path, {})
    assert "rc=1" in r["out"], r["out"]
    assert "no media with label" in r["out"]
