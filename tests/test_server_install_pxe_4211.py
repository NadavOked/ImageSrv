"""Keep the installation guide's PXE smoke check in sync with GRUB output."""

from pathlib import Path

import pytest

from boot.grub_menu import GrubConfig, render

GUIDE = Path(__file__).resolve().parents[1] / "docs/guides/server-install.md"
CONFIG = GrubConfig(server_base="http://10.44.12.10:7712")


@pytest.mark.parametrize(
    "known, role, timeout, style, default, wording",
    [
        (True, "classroom", 0, "hidden", "imagesrv", "תחנת כיתה ומחשב בנייה רשומים עולים ישירות ל-ImageSrv"),
        (True, "build", 0, "hidden", "imagesrv", "תחנת כיתה ומחשב בנייה רשומים עולים ישירות ל-ImageSrv"),
        (True, "teacher", -1, "menu", "local", "תחנת מורה רשומה מציגה תפריט GRUB שממתין לבחירה"),
        (False, None, 0, "hidden", "local", "מחשב עם MAC לא רשום עולה מיד מהדיסק המקומי"),
    ],
    ids=["classroom", "build", "teacher", "unregistered"],
)
def test_pxe_boot_matches_the_installation_guide(known, role, timeout, style, default, wording):
    config = render({"known": known, "role": role, "task": None, "session": None}, CONFIG)
    settings = config.splitlines()
    assert f"set timeout={timeout}" in settings
    assert f"set timeout_style={style}" in settings
    assert f"set default={default}" in settings

    guide = " ".join(GUIDE.read_text(encoding="utf-8").split())
    assert wording in guide, f"The guide should describe the {role or 'unregistered'} boot path: {wording}"
    assert "(../architecture/grub-generator.md)" in guide
