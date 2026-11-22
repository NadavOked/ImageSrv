"""A published document must not teach installing a server any way but the ISO / USB.

Nadav, 15/11/2026: the network install (`setup-boot-server.sh` on an existing Debian,
finished in a browser wizard on 443) is a leftover from before the ISO. The script stays
-- the ISO's first boot runs it -- but no published document may tell a reader to run it
as the way to install, or to finish an install in the wizard.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Documents a stranger opens to learn how to install; all are on the [public] list.
DOCS = (
    "README.md",
    "deploy/README.md",
    "installer/README.md",
    "tools/iso/README.md",
    "docs/guides/server-install.md",
    "docs/architecture/server.md",
    "docs/architecture/imagesrv-spec.md",
)

# `sudo ./deploy/setup-boot-server.sh`, `sudo bash deploy/setup-boot-server.sh --admin-pass ...`
MANUAL_RUN = re.compile(r"sudo\s+(?:bash\s+)?\S*setup-boot-server\.sh")
# the web wizard as a way to finish an install, or its units as something to start
WIZARD = re.compile(r"systemctl\s+start\s+imagesrv-wizard|אשף\s+ה-?HTTPS|אשף\s+ב-?443|אשף\s+דרך\s+הדפדפן")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_no_published_doc_tells_you_to_run_the_installer_by_hand():
    hits = [f"{rel}: {m.group(0)}" for rel in DOCS for m in MANUAL_RUN.finditer(_text(rel))]
    assert not hits, hits


def test_no_published_doc_offers_the_browser_wizard_as_an_install():
    hits = [f"{rel}: {m.group(0)}" for rel in DOCS for m in WIZARD.finditer(_text(rel))]
    assert not hits, hits


def test_server_install_says_iso_or_usb_is_the_only_way():
    text = _text("docs/guides/server-install.md")
    head = text[:900]
    assert "ISO" in head and "USB" in head
    assert "לא על דביאן קיים" in head


def test_server_install_keeps_the_iso_content():
    """Removing the network path must not take the ISO's own instructions with it."""
    text = _text("docs/guides/server-install.md")
    for needle in ("צריבת הדיסק-און-קי", "SHA256SUMS", "verify-boot-payload.sh",
                   "מסך השרת (DCUI)", "firstboot-answers.sh", "מערכת בלי קובץ ההגדרה מההתקנה"):
        assert needle in text, needle
