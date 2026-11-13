"""#2793: the server screen (DCUI) lists a degraded RAID array and an ESP that
did not mount under "the server needs attention" -- from the same source as the
health page (``raid_status.health_checks``), not a second reckoning of mdstat.

Found on ESXi 10/11 (v0.91.7, nt1109-r2, RAID1 with one disk gone): the
console's health page said ``raid_md0`` bad and ``raid_esp`` warn, while the
screen said "one problem" -- the deploy network only.
"""
from __future__ import annotations

from server.dcui import samples, view
from server.system import raid_status

DEGRADED = ("Personalities : [raid1]\n"
            "md0 : active raid1 sda3[0]\n"
            "      15718400 blocks super 1.2 [2/1] [U_]\n\n"
            "unused devices: <none>\n")
HEALTHY = ("Personalities : [raid1]\n"
           "md0 : active raid1 sdb3[1] sda3[0]\n"
           "      15718400 blocks super 1.2 [2/2] [UU]\n\n"
           "unused devices: <none>\n")
REBUILD = ("Personalities : [raid1]\n"
           "md0 : active raid1 sdb3[2] sda3[0]\n"
           "      15718400 blocks super 1.2 [2/1] [U_]\n"
           "      [=>...................]  recovery =  7.5% (1180000/15718400) finish=2.1min\n\n"
           "unused devices: <none>\n")
DECLARED = "level=raid1\narray=/dev/md0\n"
MOUNTED = "/dev/md0 / ext4 rw 0 0\n/dev/sda1 /boot/efi vfat rw 0 0\n"
NO_ESP = "/dev/md0 / ext4 rw 0 0\n"


def _rows(mdstat, mounts=MOUNTED, declared=DECLARED):
    return tuple(raid_status.health_checks(lambda: mdstat, lambda: declared, lambda: mounts))


def _problems(rows):
    return view.problems(samples.PRIMARY, view.Extras(raid=rows), samples.TODAY)


def test_a_degraded_array_is_a_red_problem_with_the_disk_count():
    found = [p for p in _problems(_rows(DEGRADED)) if "RAID" in p.title]
    assert [(p.sev, p.title) for p in found] == [("bad", "מערך RAID פגוע")]
    assert "1/2 כוננים" in found[0].what


def test_an_unmounted_esp_is_an_orange_problem():
    found = [p for p in _problems(_rows(HEALTHY, mounts=NO_ESP)) if "ESP" in p.title]
    assert [(p.sev, p.title) for p in found] == [("warn", "מחיצת ה-ESP לא מעוגנת")]


def test_a_rebuild_is_orange_and_a_healthy_array_is_no_problem():
    rebuild = [p for p in _problems(_rows(REBUILD)) if "RAID" in p.title]
    assert [p.sev for p in rebuild] == ["warn"] and "7.5%" in rebuild[0].what
    assert not [p for p in _problems(_rows(HEALTHY)) if "RAID" in p.title or "ESP" in p.title]


def test_an_unread_mdstat_is_never_shown_as_fine():
    def unreadable():
        raise PermissionError("denied")
    rows = tuple(raid_status.health_checks(unreadable, lambda: DECLARED, lambda: MOUNTED))
    found = [p for p in _problems(rows) if "RAID" in p.title]
    assert [p.sev for p in found] == ["bad"]   # declared but unread: raid_status says bad


def test_collect_extras_carries_the_rows_and_a_failed_check_is_not_silence(tmp_path):
    extras = view.collect_extras(None, tmp_path / "uptime", raid_rows=lambda: _rows(DEGRADED))
    assert any(row["state"] == "bad" for row in extras.raid)

    def broken():
        raise RuntimeError("boom")
    extras = view.collect_extras(None, tmp_path / "uptime", raid_rows=broken)
    assert [row["state"] for row in extras.raid] == ["unknown"]
    assert [p.title for p in _problems(extras.raid)] == ["לא הצלחנו לבדוק את מערך ה-RAID"]


def test_the_red_card_reaches_the_rendered_frame():
    runtime = samples._runtime(samples.PRIMARY, view.Extras(raid=_rows(DEGRADED)))
    frame = view.render(runtime, samples.NOW, samples.NOW, samples.CLOCK, samples.TODAY, 47)
    probs = [line.split("\t") for line in frame.split("\n") if line.startswith("prob\t")]
    assert any("מערך RAID פגוע" in "\t".join(rec) for rec in probs)
