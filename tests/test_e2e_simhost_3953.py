"""#3953: הסימולציה כ-root לא כותבת ל-`/etc/imagesrv` ולא מריצה `nft` או `systemctl`.

`tools/e2e/harness.py` (`start_server`) מרים את `server.main` כתהליך. עד #3953 `main()` קרא
ל-`_host_config_writes()` של הייצור — כותבי `/etc/imagesrv/known-macs` ו-`nft.d/known-macs.nft`
ו-`systemctl reload dnsmasq` — גם בסימולציה. עכשיו ה-bootstrap של `start_server` מחליף את
הפונקציה הזו (`tools/e2e/simhost.py`) **לפני** ש-`main` קורא לה, ו-`main()` עצמו לא משתנה.

הטסט מריץ את ה-bootstrap האמיתי של `start_server` בתהליך הטסט, כש-`main` מוחלף בבדיקה שקוראת
את מה ש-`main` היה מקבל: הכותבים האמיתיים של המארח מוחלפים ברושמים, ושום דבר אינו נכתב אליהם.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import server.main
from server.core import capabilities
from server.net import dhcp_host, known_fw
from server.rounds import chunklog
from tools.e2e import harness

MAC = "aa:bb:cc:dd:ee:01"


def _bootstrap(workdir: Path, monkeypatch) -> tuple[str, dict]:
    captured: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: captured.append((list(cmd), kw)) or object())
    harness.start_server(workdir)
    (argv, kw), = captured
    kw["stdout"].close()
    return argv[argv.index("-c") + 1], kw["env"]


def _run_bootstrap_with_probe(workdir: Path, monkeypatch) -> dict:
    """מריץ את ה-bootstrap של `start_server`; `main` הוא בדיקה שמחזירה מה ש-`main` היה מקבל."""
    bootstrap, env = _bootstrap(workdir, monkeypatch)
    host_calls: list[str] = []
    # הכותבים האמיתיים של המארח (מה ש-`_host_config_writes` של הייצור מצרף) — רושמים ולא כותבים.
    monkeypatch.setattr(dhcp_host, "apply_known_macs", lambda text: host_calls.append("known-macs"))
    monkeypatch.setattr(known_fw, "apply", lambda macs: host_calls.append("nft"))
    monkeypatch.setattr(dhcp_host, "_systemctl", lambda *a, **k: host_calls.append("systemctl"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: host_calls.append(f"run {a[0]}"))
    # מה שה-bootstrap משנה בתהליך (כמו ב-`test_e2e_chunk_sim_3173`) — מוחזר בסוף הטסט.
    monkeypatch.setattr(capabilities, "CLASSROOMS", capabilities.CLASSROOMS)
    monkeypatch.setattr(chunklog, "run_process", chunklog.run_process)
    monkeypatch.setattr(server.main, "_host_config_writes", server.main._host_config_writes)
    assert "IMAGESRV_SIM_ETC" in env, "start_server gives the server no simulation etc directory"
    for key in (harness.SIMSEND_ENV, "IMAGESRV_SIM_ETC"):
        monkeypatch.setenv(key, env[key])
    seen: dict = {}

    def probe() -> None:
        seen["writes"] = server.main._host_config_writes()

    monkeypatch.setattr(server.main, "main", probe)
    exec(bootstrap, {})
    seen["host_calls"] = host_calls
    return seen


def test_the_simulation_server_writes_the_mac_lists_only_under_its_own_directory(tmp_path, monkeypatch):
    seen = _run_bootstrap_with_probe(tmp_path, monkeypatch)
    writes = seen["writes"]
    hooks = writes["known_macs_hooks"]
    assert hooks is not None, "main() would get no known-macs writers at all"
    assert hooks["apply"]("# known macs\n" + MAC + "\n") is None
    assert hooks["firewall"]([MAC]) is None
    etc = tmp_path / "etc" / "imagesrv"
    assert MAC in (etc / "known-macs").read_text(encoding="utf-8")
    assert MAC in (etc / "nft.d" / "known-macs.nft").read_text(encoding="utf-8")
    assert seen["host_calls"] == [], "the host's writers / nft / systemctl were reached"
    assert not (writes["sync_dnsmasq"] or writes["dnsmasq_dropin"])
    assert writes["grub_password_file"] is None
    assert "firewall_stale" not in hooks      # הוא קורא nft אמיתי על המארח


def test_the_directory_reaches_the_server_process_through_the_environment(tmp_path, monkeypatch):
    bootstrap, env = _bootstrap(tmp_path, monkeypatch)
    assert env.get("IMAGESRV_SIM_ETC") == str(tmp_path / "etc" / "imagesrv")
    assert "simhost" in bootstrap and bootstrap.index("simhost") < bootstrap.index("main()")


def test_main_itself_is_unchanged(monkeypatch):
    """הפתרון אינו מוסיף פרמטרים ל-`main()` — שלושה טסטים אחרים קוראים את מבנה הפונקציה הזו."""
    import inspect
    assert list(inspect.signature(server.main.main).parameters) == []
