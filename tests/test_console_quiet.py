"""‏#1195: ה-DCUI על tty1 חייב מסך שקט — הודעות קרנל נשארות ביומן.

נמדד על ESXi 22/09: אחרי ההתקנה החיה הראשונה, שורות `imagesrv-drop IN=ens33 ...`
(‏IGMP שנזרק ע"י חומת האש, ברמת warning) נדפסו מעל ה-DCUI. שני צדדים: הקרנל
(‏`kernel.printk` — הקונסולה מקבלת רק crit ומעלה) וכלל ה-nft (‏`level info`)."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSCTL = REPO / "deploy" / "90-imagesrv-console.conf"
SETUP = REPO / "deploy" / "setup-boot-server.sh"
NFT = REPO / "deploy" / "nftables-rules.sh"


def test_the_console_sysctl_is_shipped_and_installed():
    conf = SYSCTL.read_text(encoding="utf-8")
    values = [ln.split("=", 1) for ln in conf.splitlines() if ln.strip() and not ln.startswith("#")]
    assert [(k.strip(), v.strip()) for k, v in values] == [("kernel.printk", "3 4 1 3")]
    setup = SETUP.read_text(encoding="utf-8")
    assert 'install -m 0644 "$APP_DIR/deploy/90-imagesrv-console.conf" /etc/sysctl.d/90-imagesrv-console.conf' in setup
    # applied now, not only on the next boot -- and a failure is said, not swallowed
    assert "sysctl -q -p /etc/sysctl.d/90-imagesrv-console.conf || warn" in setup


def test_the_drop_log_rule_is_info_not_warning():
    nft = NFT.read_text(encoding="utf-8")
    assert 'log prefix "imagesrv-drop " level info limit rate 5/minute' in nft
    assert 'log prefix "imagesrv-drop " limit rate' not in nft, "כלל log בלי level = warning = מודפס על הקונסולה"


def test_the_installer_does_not_touch_sshd():
    """‏#1266: SSH אינו חלק מהמוצר — המתקין אינו כותב drop-in ל-sshd ואינו
    טוען אותו מחדש (עד #1266 הוא סגר sshd קיים ללולאה המקומית, #729)."""
    setup = SETUP.read_text(encoding="utf-8")
    assert "/etc/ssh" not in setup
    assert "systemctl reload ssh" not in setup
    assert "ssh_switch" not in setup
