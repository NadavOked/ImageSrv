"""#2489 — leads from the review of the server side that talks to the agents.

The items fixed here (the numbers are the Issue's; 1 waits for Nadav, and 5
and 8 are in `test_interserver_2488_2489.py`):

* **2** — `GET /api/v1/agent/sessions/active` had no gate. It names the MAC,
  the name and the disk events of every member. Now `source_gate`: the source
  address is leased to a registered machine (as the manifest the agent pulls
  the same way, with no MAC).
* **3** — with no sign-in and no password, `/agent/pulls` took `opened_by`
  from `username` in the body: `"admin"` showed in the overview as an admin.
  Now an unproven name is not attribution -- `opened_by` is empty.
* **4** — a partition file named `manifest.unverified` was overwritten by the
  manifest in `_admit`; on the streaming import (secondary) the digest kept
  from the extraction matched, and the image entered the library without its
  partition (principle 6). The reserved name is refused at the member.
* **6** — a refusal journal line is capped per event (#2498), but one source
  sending a new MAC on every request used the whole cap alone, and another
  machine's refusal was then only counted. Now a second cap per source address.
* **7** — `all_macs` had no item cap and a quadratic dedupe (a 1 MiB body held
  ~52k addresses, each with a query). Now the first `HELLO_MAX_ALL_MACS` items,
  deduped with a set.

Negative control: with every production file of this PR back at origin/main,
every test here fails except the ones marked "control".
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import MANIFEST_256, hello_body, setup_classroom, write_image
from server import db
from server.library import archive
from server.library.images import ImageLibrary
from test_machine_identity import (BUILD, MAC1, MAC2, STATION_IP, STRANGER_IP,  # noqa: F401
                                   identity_server, journal_details)
from test_server_streams import VLAN, second_class

OTHER_IP = "10.44.12.98"


# --- 2: GET /agent/sessions/active ----------------------------------------------------


def test_sessions_active_refuses_an_address_without_a_registered_machine(identity_server):
    setup_classroom(identity_server)
    response = identity_server["stranger"].get("/api/v1/agent/sessions/active")
    assert response.status_code == 403, response.text
    assert response.json()["code"].startswith("identity_")
    assert any("(sessions/active)" in detail for _u, detail in
               journal_details(identity_server, response.json()["code"]))


def test_sessions_active_still_answers_the_registered_machine(identity_server):
    """Control: the station's GUI (`guistate.sh`) calls it from its own lease."""
    setup_classroom(identity_server)
    response = identity_server["station"].get("/api/v1/agent/sessions/active")
    assert response.status_code == 200, response.text
    assert response.json() == {"session": None}


# --- 3: opened_by of /agent/pulls -----------------------------------------------------


@pytest.mark.usefixtures("classrooms_on")
def test_a_pull_without_a_sign_in_is_not_opened_in_the_name_from_the_body(server):
    lab2 = second_class(server)
    db.set_setting(server["ctx"].conn, "recovery_require_login", "false")
    response = server["anon"].post(VLAN + "/api/v1/agent/pulls", json={
        "mac": lab2["mac1"], "image_id": MANIFEST_256["id"],
        "username": "admin", "password": ""})
    assert response.status_code == 200, response.text
    row = server["ctx"].conn.execute(
        "SELECT opened_by FROM sessions WHERE id = ?", (response.json()["id"],)).fetchone()
    assert row["opened_by"] == "", dict(row)


@pytest.mark.usefixtures("classrooms_on")
def test_a_pull_with_a_password_keeps_the_proven_name(server):
    """Control: where the password is checked, the name is proven and stays."""
    lab2 = second_class(server)
    response = server["anon"].post(VLAN + "/api/v1/agent/pulls", json={
        "mac": lab2["mac1"], "image_id": MANIFEST_256["id"],
        "username": "labtech", "password": "deploy-pass-1"})
    assert response.status_code == 200, response.text
    row = server["ctx"].conn.execute(
        "SELECT opened_by FROM sessions WHERE id = ?", (response.json()["id"],)).fetchone()
    assert row["opened_by"] == "labtech"


# --- 4: manifest.unverified as a partition file ---------------------------------------


def _tar_with_a_reserved_partition_name(tmp_path) -> bytes:
    manifest = {**MANIFEST_256, "partitions": [
        {**MANIFEST_256["partitions"][0], "file": archive.UNVERIFIED},
        MANIFEST_256["partitions"][1]]}
    src = tmp_path / "src"
    write_image(src, manifest)
    return b"".join(archive.tar_stream(src / manifest["id"], manifest["id"]))


def test_the_streaming_import_refuses_a_partition_named_manifest_unverified(tmp_path):
    body = _tar_with_a_reserved_partition_name(tmp_path)
    root = tmp_path / "lib"
    root.mkdir()
    with pytest.raises(archive.ArchiveError) as exc:
        archive.import_tar_stream(io.BytesIO(body), root, set(), max_bytes=len(body))
    assert archive.UNVERIFIED in str(exc.value)
    assert ImageLibrary(root).scan() == {}


def test_the_file_import_refuses_it_too(tmp_path):
    """Control: `import_tar` refused it before too (the digest is computed after
    the replace) -- now by name, at the member."""
    path = tmp_path / "x.tar"
    path.write_bytes(_tar_with_a_reserved_partition_name(tmp_path))
    root = tmp_path / "lib"
    root.mkdir()
    with pytest.raises(archive.ArchiveError):
        archive.import_tar(path, root, set())
    assert ImageLibrary(root).scan() == {}


# --- 6: a second cap per source address ----------------------------------------------


def _unleased(n: int) -> str:
    return f"02:89:00:00:{n // 256:02x}:{n % 256:02x}"


def test_one_source_does_not_use_the_whole_journal_cap_alone(identity_server):
    from server.agent import api as agent_api
    from server.console.journal_he_detail import he_detail
    per_source = getattr(agent_api, "JOURNAL_KEYS_PER_SOURCE", 8)
    flood = agent_api.JOURNAL_KEYS_PER_WINDOW + 8
    for n in range(flood):
        r = identity_server["stranger"].post("/api/v1/agent/hello", json=hello_body(_unleased(n)))
        assert r.status_code == 403, r.text
    other = TestClient(identity_server["app"], client=(OTHER_IP, 40020))
    assert other.post("/api/v1/agent/hello",
                      json=hello_body("02:89:00:01:00:01")).status_code == 403
    lines = [d for _u, d in journal_details(identity_server, "identity_unverifiable")]
    assert sum(f"from {STRANGER_IP}:" in d for d in lines) == per_source, lines
    assert any(d.startswith(f"02:89:00:01:00:01 from {OTHER_IP}") for d in lines), lines
    floods = [d for _u, d in journal_details(identity_server, "journal_flood")]
    assert any(STRANGER_IP in d for d in floods), floods
    for line in floods:
        assert he_detail("journal_flood", line) != line, line


def test_a_machine_with_few_refusals_is_journaled_each_time(identity_server):
    """Control: under the per-source cap every different refusal is a line."""
    for n in range(3):
        identity_server["stranger"].post("/api/v1/agent/hello", json=hello_body(_unleased(n)))
    lines = journal_details(identity_server, "identity_unverifiable")
    assert len(lines) == 3, lines
    assert journal_details(identity_server, "journal_flood") == []


# --- 7: all_macs --------------------------------------------------------------------


def _junk(count: int) -> list[str]:
    return [f"02:77:00:00:{n // 256:02x}:{n % 256:02x}" for n in range(count)]


def test_all_macs_past_the_cap_do_not_identify(identity_server):
    """`BUILD` is leased to the station but not registered; `MAC2` is registered
    with no lease and was not seen -- #524 lets it identify (item 1, open). Past
    the first 16 items it is not read at all."""
    from server.agent import api as agent_api
    setup_classroom(identity_server)
    cap = getattr(agent_api, "HELLO_MAX_ALL_MACS", 16)
    body = {**hello_body(BUILD), "all_macs": _junk(cap) + [MAC2]}
    response = identity_server["station"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["known"] is False


def test_a_registered_card_inside_the_cap_still_identifies(identity_server):
    """Control (#524): the same card as the first item identifies, as before."""
    setup_classroom(identity_server)
    body = {**hello_body(BUILD), "all_macs": [BUILD, MAC2]}
    response = identity_server["station"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["known"] is True
