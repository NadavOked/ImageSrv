"""#3686 (אפיק #3653): רמת הדחיסה של קליטת דיסק VM באה מהמוח (`ingest.compression()`), לא מקבוע מקביל ב-`vmdisk`.

הערך נשאר 3: הטסט משנה את הרמה של המוח ל-7 ומוכיח ש-`vmdisk` עוקב אחריה, בשני המקומות שהשתמשו בקבוע --
פקודת הדחיסה של כל חתיכה, והשדה `compression` שבמניפסט.
"""

from __future__ import annotations

import io

from server.brain import ingest
from server.vm import vmdisk


class _Reader:
    """`partclone.dd` מזויף: זרם קטן ואז סוף."""

    def __init__(self, data: bytes):
        self.stdout = io.BytesIO(data)
        self.returncode = 0

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


class _Squeeze:
    returncode = 0

    def communicate(self, data=None, timeout=None):
        return b"frame", None


class _Host:
    def __init__(self, data: bytes):
        self.data = data
        self.zstd_argv: list[list[str]] = []

    def popen(self, argv, **_kw):
        if argv[0] == "zstd":
            self.zstd_argv.append(argv)
            return _Squeeze()
        return _Reader(self.data)


def _zstd_levels(tmp_path) -> list[str]:
    host = _Host(b"\x01" * 4096)
    try:
        vmdisk.capture_partition(host, "unknown", "/dev/loop7", tmp_path / "p2.msr.pcl.zst", tmp_path, 2, lambda _n: None)
    except Exception:                    # הזרם המזויף אינו חתיכה אמיתית; מה שנבדק הוא הפקודה שנשלחה ל-zstd
        pass
    return [a for argv in host.zstd_argv for a in argv if a.startswith("-") and a[1:].isdigit()]


def test_the_chunk_compressor_runs_at_the_brains_level(tmp_path, monkeypatch):
    assert _zstd_levels(tmp_path) == ["-3"]                       # היום: אותו ערך
    monkeypatch.setattr(ingest, "LEVEL", 7)
    assert _zstd_levels(tmp_path) == ["-7"]                       # המוח שינה, השרת עוקב


def test_the_manifest_reports_the_brains_level(monkeypatch):
    assert vmdisk.compression_tag() == "zstd-3"
    monkeypatch.setattr(ingest, "LEVEL", 7)
    assert vmdisk.compression_tag() == "zstd-7"
    assert ingest.applied_level({"compression": vmdisk.compression_tag()}) == 7   # הרמה נקראת בחזרה כמו שהמוח קורא מניפסט
