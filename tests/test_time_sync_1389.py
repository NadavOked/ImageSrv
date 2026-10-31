from server.system import health
from pathlib import Path


def test_time_sync_health_requires_active_and_synchronized():
    assert health.time_sync_check("active", "yes")["state"] == "ok"
    assert health.time_sync_check("inactive", "yes")["state"] == "warn"
    assert health.time_sync_check("active", "no")["state"] == "warn"
    assert health.time_sync_check("", "")["state"] == "off"


def test_installer_enables_timesyncd_for_the_first_server_boot():
    finish = (Path(__file__).parent.parent / "installer" / "lib" / "finish.sh").read_text(
        encoding="utf-8")
    assert 'chroot "$TARGET" systemctl enable chrony.service' in finish
