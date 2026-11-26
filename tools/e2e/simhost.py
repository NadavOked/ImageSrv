"""#3953: הסימולציה לא נוגעת במארח.

`server.main.main()` מדליק בעלייה כותבים ל-`/etc/imagesrv` (רשימת ה-MAC המוכרים ל-dnsmasq
ול-nftables) ומריץ `nft` ו-`systemctl`. כשהסימולציה רצה כ-root זה דורס את הרשימה של שרת
חי, או נכשל בגלוי על מכונה בלי ImageSrv. כאן מוחלף **רק** `_host_config_writes` — הפונקציה
ש-`main` קורא לה — בכותבים שכותבים את אותם קבצים באותו פורמט לתיקייה של הסימולציה, בלי `nft`
ובלי `systemctl`. `main()` עצמו לא משתנה, כמו שהטסטים מזריקים `dhcp_hooks` ל-`create_app`.

נטען בתהליך של השרת לפני `server.main.main()` (`harness.start_server`), כמו `simsend`.
"""

from __future__ import annotations

from pathlib import Path

#: שם משתנה הסביבה שבו `harness.start_server` מעביר את תיקיית הסימולציה.
ENV = "IMAGESRV_SIM_ETC"


def writes(config_dir: Path) -> dict:
    """אותו מבנה כמו `server.main._host_config_writes`, כשכל הכתיבות הן אל `config_dir`."""
    from server.core.atomicfile import write_atomic
    from server.net import known_fw

    nft_dir = config_dir / "nft.d"
    nft_dir.mkdir(parents=True, exist_ok=True)

    def apply(text: str) -> None:
        write_atomic(config_dir / "known-macs", text)

    def firewall(macs: list[str]) -> None:
        write_atomic(nft_dir / "known-macs.nft", known_fw.render(macs))

    return {"known_macs_hooks": {"apply": apply, "firewall": firewall},
            "sync_dnsmasq": False, "dnsmasq_dropin": False, "grub_password_file": None}


def install(config_dir: Path) -> None:
    """מחליף את `server.main._host_config_writes` לפני שה-`main` קורא לו."""
    import server.main

    server.main._host_config_writes = lambda: writes(config_dir)
