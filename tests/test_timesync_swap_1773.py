"""‏#1773 + ‏#1814 — שרת **שמתעדכן** עובר מ-systemd-timesyncd ל-chrony, ושעון שקפץ חוזר.

הכרעת נדב (19/10, PR #1834, אפשרות ב'): ההחלפה היא שלב מפורש של זוג קבוע
(``deploy/timesync-swap.sh``), ו-``deploy/security-upgrade.sh`` נשאר עם ``--no-remove``
לכל השאר. עד כה chrony נכנסה לקריאת apt הכללית, ``--no-remove`` סירב להסיר את
systemd-timesyncd, וכל עדכון של שרת קיים חזר לתג הקודם.

מה שנבדק:

* ``security-upgrade.sh``: chrony אינה בקריאת ה-apt הכללית כש-timesyncd מותקנת; השלב
  הנפרד נקרא עם המאגר של התג; כשל שלו = כשל השלב, בשמו.
* ``timesync-swap.sh``: בדיקות מקדימות שלא נוגעות בדבר (דרך חזרה במאגר, הסרה אחת בלבד),
  שרת הזמן של timesyncd עובר להגדרה, chrony שלא מסתנכרן מחזיר ל-timesyncd **בגלוי**, וגם
  כשל החזרה נאמר בשמו.
* ‏#1814: ``makestep 1.0 -1`` בהגדרה ש-server/system/timesync.py כותב (התקנה ועדכון), ושורת
  "שעון" בבריאות: מקור, מסונכרן או לא, וסטייה — עם כיוון וסף.

הכלים החיצוניים (apt, ‏dpkg-query, ‏systemctl, ‏chronyc, ‏timedatectl, ‏systemd-analyze)
מדומים על PATH; python, ‏zcat ו-timeout אמיתיים. ההרצה האמיתית — ב-chroot של Debian 13 (PR).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from server.system import health, timesync
from test_installer_1131 import calls, fake_tool, posix, run_bash
from test_security_pool_1411 import OLD, POOL, POSIX, World, _make_pool, _packages_index, _pool_tools

ROOT = Path(__file__).resolve().parent.parent
SWAP = ROOT / "deploy" / "timesync-swap.sh"
SYSTEMD = "257.9-1~deb13u1"
CHRONY = "4.6.1-3"


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


# ---- #1814: the step rule and the health line ----------------------------------------------

def test_the_client_conf_steps_on_every_update_and_debians_rule_is_retired(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    monkeypatch.setattr(timesync, "CLIENT_CONF", tmp_path / "imagesrv.conf")
    main = tmp_path / "chrony.conf"
    main.write_text("pool 2.debian.pool.ntp.org iburst\nmakestep 1 3\nrtcsync\n", encoding="utf-8")
    monkeypatch.setattr(timesync, "MAIN_CONF", main)
    timesync._ensure_confdir()
    timesync._write_client("10.0.0.5")
    client = timesync.CLIENT_CONF.read_text(encoding="utf-8").splitlines()
    assert client.count("makestep 1.0 -1") == 1 and "server 10.0.0.5 iburst" in client
    active = [ln for ln in main.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln.startswith("makestep") for ln in active), active
    assert "rtcsync" in active


@pytest.mark.parametrize("text,ms", [("0.001250 seconds slow of NTP time", -1.25),
                                     ("180.250000 seconds fast of NTP time", 180250.0),
                                     ("0.000002 seconds fast of NTP time", 0.002)])
def test_the_offset_keeps_its_direction(text, ms):
    assert timesync._offset_ms(text) == pytest.approx(ms)


@pytest.mark.parametrize("status,state,words", [
    ({"synchronized": True, "server": "10.0.0.5", "offset_ms": 0.42}, "ok",
     ["מקור 10.0.0.5", "מסונכרן", "סטייה +0.420 ms"]),
    ({"synchronized": False, "server": None, "offset_ms": 3.0}, "warn", ["מקור לא ידוע", "לא מסונכרן"]),
    ({"synchronized": True, "server": "10.0.0.5", "offset_ms": 2500.0}, "warn", ["השעון מקדים ב-2 ש'"]),
    ({"synchronized": True, "server": "10.0.0.5", "offset_ms": -180000.0}, "bad",
     ["השעון מאחר ב-180 ש'", "קודי MFA לא יתקבלו"]),
    ({"synchronized": None}, "off", ["לא נבדק"]),
])
def test_the_clock_line_names_source_sync_and_offset(status, state, words):
    row = health.time_sync_check(status)
    assert row["label"] == "שעון" and row["state"] == state, row
    for word in words:
        assert word in row["detail"], row


# ---- deploy/security-upgrade.sh: chrony goes to its own step -------------------------------

TIMESYNCD = {"systemd-timesyncd": SYSTEMD}
POOL_CLOCK = {**POOL, "chrony": CHRONY, "systemd-timesyncd": SYSTEMD}


def _swap_stub(world: World) -> Path:
    """The swap step as security-upgrade sees it: arguments logged, the pair swapped."""
    installed = posix(world.tmp / "installed")
    stub = world.repo / "deploy" / "timesync-swap.sh"
    stub.write_text(f'''#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_LOG.swap"
if [ "${{FAKE_SWAP_RC:-0}}" != 0 ]; then
  echo "החלפת השעון ל-chrony נכשלה: chrony לא הסתנכרן תוך 60 ש' — חזר ל-systemd-timesyncd"
  exit "$FAKE_SWAP_RC"
fi
awk '$1 != "systemd-timesyncd"' "{installed}" > "{installed}.sw" && echo "chrony {CHRONY}" >> "{installed}.sw"
mv "{installed}.sw" "{installed}"
echo "שעון: chrony במקום systemd-timesyncd"
''', encoding="utf-8", newline="\n")
    return world.tmp / "calls.log.swap"


@POSIX
def test_chrony_is_swapped_in_its_own_step_not_in_the_no_remove_install(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_CLOCK, installed={**OLD, **TIMESYNCD},
                  wanted=[*OLD, "chrony"])
    swap_args = _swap_stub(world)
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    installs = [c for c in world.calls("apt-get") if " install " in c]
    assert len(installs) == 1 and "chrony" not in installs[0].split(), installs
    assert "--no-remove" in installs[0]
    args = swap_args.read_text(encoding="utf-8").splitlines()
    assert args[0] == posix(world.repo) and args[1].endswith("/security-pool/repo"), args
    assert any(a.startswith("Dir::Etc::SourceList=") for a in args), args
    assert world.installed()["chrony"] == CHRONY and "systemd-timesyncd" not in world.installed()
    assert world.state()["installed"] == "1"


@POSIX
def test_a_failed_swap_fails_the_packages_step_with_its_reason(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_CLOCK, installed={**OLD, **TIMESYNCD},
                  wanted=[*OLD, "chrony"])
    _swap_stub(world)
    done = world.run(FAKE_SWAP_RC="2")
    assert done.returncode == 8, done.stdout + done.stderr
    last = done.stdout.splitlines()[-1]
    assert "חזר ל-systemd-timesyncd" in last, last
    assert world.state() is None


@POSIX
def test_chrony_without_timesyncd_is_an_ordinary_new_package(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_CLOCK, installed=OLD, wanted=[*OLD, "chrony"])
    swap_args = _swap_stub(world)
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    install = next(c for c in world.calls("apt-get") if " install " in c)
    assert install.endswith(" chrony") and not swap_args.exists()


# ---- deploy/timesync-swap.sh -----------------------------------------------------------------

class Box:
    """A server with systemd-timesyncd, a pool dir, chrony's /etc and the DB."""

    def __init__(self, tmp: Path, bindir: Path, *, pool: dict[str, str] | None = None,
                 ntp: str = "", db_value: str = ""):
        from server.db import connect, set_setting
        self.tmp, self.bindir = tmp, bindir
        self.state = tmp / "dpkg"
        self.state.write_text(f"systemd {SYSTEMD}\nsystemd-timesyncd {SYSTEMD}\n",
                              encoding="utf-8", newline="\n")
        self.pool = tmp / "pool"
        index = self.pool / "dists" / "trixie" / "main" / "binary-amd64"
        index.mkdir(parents=True)
        (index / "Packages.gz").write_bytes(_packages_index(
            pool if pool is not None else {"chrony": CHRONY, "systemd-timesyncd": SYSTEMD}))
        self.etc = tmp / "chrony"
        self.etc.mkdir()
        (self.etc / "chrony.conf").write_text("pool 2.debian.pool.ntp.org iburst\nmakestep 1 3\n",
                                              encoding="utf-8", newline="\n")
        self.data = tmp / "data"
        self.data.mkdir()
        conn = connect(self.data / "imagesrv.db")
        if db_value:
            set_setting(conn, timesync.SETTING, db_value)
        (tmp / "timesyncd.conf").write_text(f"[Time]\n#NTP=\n{ntp}\n", encoding="utf-8", newline="\n")
        self._tools()

    def _tools(self) -> None:
        state = posix(self.state)
        fake_tool(self.bindir, "dpkg-query", f'''
for last; do :; done
v=$(awk -v p="$last" '$1 == p {{print $2}}' "{state}")
[ -n "$v" ] || exit 1
printf 'install ok installed %s' "$v"''')
        fake_tool(self.bindir, "apt-get", f'''
case " $* " in
  *" -s "*) echo "Remv systemd-timesyncd [{SYSTEMD}]"; [ -z "${{FAKE_SIM_EXTRA:-}}" ] || echo "$FAKE_SIM_EXTRA"
            echo "Inst chrony ({CHRONY} Debian:13.1/stable [amd64])"; exit 0 ;;
  *" install chrony "*) [ "${{FAKE_APT_CHRONY_RC:-0}}" = 0 ] || {{ echo "E: dpkg failed"; exit "$FAKE_APT_CHRONY_RC"; }}
            awk '$1 != "systemd-timesyncd"' "{state}" > "{state}.n"; echo "chrony {CHRONY}" >> "{state}.n" ;;
  *" install systemd-timesyncd="*) [ "${{FAKE_APT_TS_RC:-0}}" = 0 ] || {{ echo "E: Unable to locate"; exit "$FAKE_APT_TS_RC"; }}
            awk '$1 != "chrony"' "{state}" > "{state}.n"; echo "systemd-timesyncd {SYSTEMD}" >> "{state}.n" ;;
  *) echo "unexpected apt-get $*" >&2; exit 64 ;;
esac
mv "{state}.n" "{state}"''')
        fake_tool(self.bindir, "systemctl", f'''
case "$1 $2" in
  "is-enabled chrony") echo enabled ;;
  "is-active --quiet") grep -q "^$3 " "{state}" ;;
esac''')
        fake_tool(self.bindir, "chronyc", '''
case "$*" in
  *tracking*) [ -n "${FAKE_UNSYNCED:-}" ] && leap="Not synchronised" || leap=Normal
              printf 'Reference ID : 0A000005\\nSystem time : 0.000100 seconds fast of NTP time\\nLeap status : %s\\n' "$leap" ;;
  *sources*) [ -n "${FAKE_UNSYNCED:-}" ] && m='^?' || m='^*'
             printf 'MS Name/IP address\\n%s 10.0.0.5 2 6 377 10 -10us[ -20us] +/- 1ms\\n' "$m" ;;
esac''')
        fake_tool(self.bindir, "timedatectl", 'echo "${FAKE_WAS_SYNCED:-yes}"')
        fake_tool(self.bindir, "systemd-analyze", f'cat "{posix(self.tmp / "timesyncd.conf")}"')

    def run(self, **env: str):
        full = {"IMAGESRV_DATA_DIR": posix(self.data), "IMAGESRV_PYTHON": sys.executable,
                "IMAGESRV_CHRONY_ETC": posix(self.etc), "IMAGESRV_CLOCK_WAIT": "1", **env}
        return run_bash(f'bash "{posix(SWAP)}" "{posix(ROOT)}" "{posix(self.pool)}" -o Dir::Cache=/x',
                        self.tmp, self.bindir, env=full)

    def installed(self) -> dict[str, str]:
        rows = self.state.read_text(encoding="utf-8").splitlines()
        return dict(r.split(" ", 1) for r in rows if r.strip())

    def setting(self) -> str | None:
        from server.db import connect, get_setting
        return get_setting(connect(self.data / "imagesrv.db"), timesync.SETTING)

    def apt(self) -> list[str]:
        return [c for c in calls(self.tmp) if c.startswith("apt-get ")]


@POSIX
def test_the_swap_installs_chrony_and_carries_the_organisations_ntp_server(tmp_path, bindir):
    box = Box(tmp_path, bindir, ntp="NTP=10.0.0.5 ntp.college.example")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "chrony" in box.installed() and "systemd-timesyncd" not in box.installed()
    real = [c for c in box.apt() if " -s " not in c]
    assert len(real) == 1 and real[0].endswith(" install chrony") and "--no-install-recommends" in real[0]
    client = (box.etc / "conf.d" / "imagesrv.conf").read_text(encoding="utf-8")
    assert "makestep 1.0 -1" in client and "server 10.0.0.5 iburst" in client, client
    assert "server ntp.college.example iburst" in client
    assert box.setting() == "10.0.0.5 ntp.college.example"
    assert "chrony במקום systemd-timesyncd" in done.stdout.splitlines()[-1]


@POSIX
def test_the_db_setting_wins_over_timesyncd_and_is_left_alone(tmp_path, bindir):
    box = Box(tmp_path, bindir, ntp="NTP=10.9.9.9", db_value="10.0.0.5")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    client = (box.etc / "conf.d" / "imagesrv.conf").read_text(encoding="utf-8")
    assert "server 10.0.0.5 iburst" in client and "10.9.9.9" not in client
    assert box.setting() == "10.0.0.5"


@POSIX
def test_no_way_back_in_the_pool_means_no_swap_and_nothing_touched(tmp_path, bindir):
    box = Box(tmp_path, bindir, pool={"chrony": CHRONY})
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    last = done.stdout.splitlines()[-1]
    assert "לא שונה דבר" in last and "אין דרך חזרה" in last, last
    assert not box.apt() and "systemd-timesyncd" in box.installed()


@POSIX
def test_a_resolution_that_removes_anything_else_is_refused_before_install(tmp_path, bindir):
    box = Box(tmp_path, bindir)
    done = box.run(FAKE_SIM_EXTRA="Remv ifenslave [2.14]")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "ifenslave" in done.stdout.splitlines()[-1]
    assert all(" -s " in c for c in box.apt()) and "chrony" not in box.installed()


@POSIX
def test_chrony_that_does_not_sync_goes_back_to_timesyncd_in_the_open(tmp_path, bindir):
    box = Box(tmp_path, bindir, ntp="NTP=10.0.0.5")
    done = box.run(FAKE_UNSYNCED="1")
    assert done.returncode == 2, done.stdout + done.stderr
    last = done.stdout.splitlines()[-1]
    assert "chrony לא הסתנכרן" in last and "חזר ל-systemd-timesyncd" in last, last
    assert box.apt()[-1].endswith(f" install systemd-timesyncd={SYSTEMD}")
    assert "systemd-timesyncd" in box.installed() and "chrony" not in box.installed()
    assert box.setting() is None


@POSIX
def test_unsynced_before_and_after_is_a_warning_not_a_rollback(tmp_path, bindir):
    box = Box(tmp_path, bindir)
    done = box.run(FAKE_UNSYNCED="1", FAKE_WAS_SYNCED="no")
    assert done.returncode == 0, done.stdout + done.stderr
    assert any(ln.startswith("אזהרה: השעון אינו מסונכרן") for ln in done.stdout.splitlines()), done.stdout
    assert "chrony" in box.installed()


@POSIX
def test_a_way_back_that_fails_says_the_server_has_no_managed_clock(tmp_path, bindir):
    box = Box(tmp_path, bindir)
    done = box.run(FAKE_UNSYNCED="1", FAKE_APT_TS_RC="100")
    assert done.returncode == 3, done.stdout + done.stderr
    assert "השרת בלי שעון מנוהל" in done.stdout.splitlines()[-1]


# ---- tools/iso/make-pool.sh --security: the way back travels with chrony ---------------------

SIM_CLOCK = f"""Inst chrony ({CHRONY} Debian:13.1/stable [amd64])
Inst systemd ({SYSTEMD} Debian-Security:13/stable-security [amd64])
"""


@POSIX
def test_make_pool_packs_systemd_timesyncd_at_systemds_version_beside_chrony(tmp_path, bindir):
    _pool_tools(tmp_path, bindir, sim=SIM_CLOCK)
    (tmp_path / "packages.txt").write_text("chrony\nsystemd\n", encoding="utf-8", newline="\n")
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(tmp_path / "out"))
    assert done.returncode == 0, done.stdout + done.stderr
    dl = next(row for row in calls(tmp_path) if row.startswith("apt-get download"))
    assert dl.split()[2:] == [f"chrony={CHRONY}", f"systemd={SYSTEMD}", f"systemd-timesyncd={SYSTEMD}"], dl


@POSIX
def test_make_pool_refuses_chrony_without_a_systemd_version_for_the_way_back(tmp_path, bindir):
    _pool_tools(tmp_path, bindir, sim=SIM_CLOCK.replace("Inst systemd (", "Inst libsystemd0 ("))
    (tmp_path / "packages.txt").write_text("chrony\n", encoding="utf-8", newline="\n")
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(tmp_path / "out"))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "systemd-timesyncd" in done.stderr and not (tmp_path / "out" / "security-pool.json").exists()
