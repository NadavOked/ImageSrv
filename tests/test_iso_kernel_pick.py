"""build-iso בוחר את הקרנל הגבוה ב-pool לפי גרסה, לא לפי נתיב (12/10).

ה-netinst מביא קרנל ישן ב-pool/main/l/linux-signed-amd64/, וה-pool שלנו
קרנל חדש יותר ב-pool/main/imagesrv/. מיון לפי נתיב בחר את הישן, וה-initramfs
(שנבנה מול החדש) נדחה ב"אי-התאמה". הטסט מריץ את שורת הבחירה עצמה מהסקריפט."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "iso" / "build-iso.sh"


def _pick_line():
    lines = [l for l in SCRIPT.read_text(encoding="utf-8").splitlines() if l.startswith("kdeb=$(find")]
    assert len(lines) == 1, lines
    return lines[0]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_newest_kernel_wins_even_when_its_path_sorts_first(tmp_path):
    old = tmp_path / "pool/main/l/linux-signed-amd64/linux-image-6.12.107+deb13-amd64_6.12.107-1_amd64.deb"
    new = tmp_path / "pool/main/imagesrv/linux-image-6.12.111+deb13-amd64_6.12.111-1_amd64.deb"
    for p in (old, new):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"")
    script = f'ISO_TREE="{tmp_path.as_posix()}"\n{_pick_line()}\nprintf "%s" "$kdeb"\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=True).stdout
    assert out.endswith("linux-image-6.12.111+deb13-amd64_6.12.111-1_amd64.deb"), out
