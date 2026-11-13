"""התראות CodeQL בציבורי (06/11) שאינן #2510 — מה שהשתנה בהתנהגות.

כל התראה כאן נבדקה מול origin/main: חלקן חולשה תאורטית בלבד (הקלט כבר מאומת
במקום אחר), והקוד שונה כך שהבדיקה יושבת **בנקודת השימוש**, בצורה שהניתוח מזהה.
הטסטים מצמידים את מה שהשינוי עושה בפועל, לא את ההתראה.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time

import pytest

from server.storage import locations as sl


# --- #866: ReDoS בקישור ה-by-path של iSCSI ---------------------------------------------

def test_the_iscsi_device_check_is_linear_on_a_hostile_link():
    """לפני: `-iscsi-` יכול היה ליפול בשני המקטעים, והחיפוש ריבועי — 8,000
    חזרות לקחו שניות. הפורטל הוא כתובת IP ופורט, בלי `-`, ולכן הגבול יחיד."""
    hostile = "/dev/disk/by-path/ip-!" + "-iscsi-!" * 8000 + "\n"
    started = time.perf_counter()
    assert sl.valid_iscsi_device(hostile) is False
    assert time.perf_counter() - started < 0.5


def test_real_by_path_links_still_pass():
    for link in ("/dev/disk/by-path/ip-10.44.3.75:3260-iscsi-iqn.2005-10.org.freenas.ctl:images-lun-0",
                 "/dev/disk/by-path/ip-[fe80::1]:3260-iscsi-iqn.2001-04.com.example:t-1-lun-12"):
        assert sl.valid_iscsi_device(link), link


# --- #652/#653: TLS 1.3 בלבד, גם בצורה שהניתוח קורא ------------------------------------

@pytest.fixture()
def contexts(monkeypatch):
    """כל `SSL.Context` שנוצר בזמן הטסט — כדי לבדוק את ה-context שהקוד בנה בעצמו."""
    SSL = pytest.importorskip("OpenSSL.SSL")
    made = []

    class Recording(SSL.Context):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(SSL, "Context", Recording)
    return made


def _below_13_refused(ctx) -> bool:
    from OpenSSL import SSL
    wanted = SSL.OP_NO_SSLv3 | SSL.OP_NO_TLSv1 | SSL.OP_NO_TLSv1_1 | SSL.OP_NO_TLSv1_2
    return ctx.set_options(0) & wanted == wanted


def test_every_interserver_context_refuses_everything_below_tls13(contexts, monkeypatch):
    from server.interserver import auth as interserver_auth, storage_client

    def no_network(*_a, **_k):
        raise ConnectionRefusedError("test: no network")

    cert, key = interserver_auth.generate_self_signed("primary", ip_sans=["127.0.0.1"])
    interserver_auth.build_server_tls_context(cert, key)
    interserver_auth.build_pinned_client_context("aa" * 32, cert, key)
    monkeypatch.setattr(storage_client.socket, "create_connection", no_network)
    with pytest.raises(ConnectionRefusedError):
        storage_client.fetch_server_spki("10.0.0.9", 8443)     # ה-preview של ה-SPKI
    assert len(contexts) == 3
    assert [_below_13_refused(ctx) for ctx in contexts] == [True, True, True]


# --- #782-787/#791: נתיב קובץ הטוקן נשאר בתוך secondaries/ -------------------------------

def test_a_token_path_that_would_leave_secondaries_is_refused(server, tmp_path, monkeypatch):
    """`valid_node_id` הוא השומר הראשי; כאן הוא מנוטרל כדי להראות שהבדיקה
    בנקודת הכתיבה עוצרת לבד — 400, ושום קובץ לא נכתב מחוץ לתיקייה."""
    from server.interserver import storage_client, storage_nodes
    monkeypatch.setattr(storage_client, "pair_secondary", lambda *a, **k: {"token": "fake-token"})
    monkeypatch.setattr(storage_nodes, "valid_node_id", lambda value: True)
    r = server["admin"].post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.30:8443/api/interserver/v1", "label": "imagesrv-haifa",
        "code": "ABC-DEF-GHJ", "expected_secondary_spki": "/../../escaped9",
        "protocol_version": "2.1"})
    assert r.status_code == 400, r.text
    assert not [p for p in tmp_path.rglob("*escaped*")]


# --- #616-618: יעד הגיבוי מנורמל לפני הבדיקה --------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="check_dest takes POSIX paths; the server is Linux")
def test_the_backup_destination_is_normalised_before_it_is_checked(tmp_path):
    from server.system import backup_job
    folder = tmp_path / "nas"
    folder.mkdir()
    dotted = f"{tmp_path.as_posix()}/./nas/."
    assert backup_job.check_dest(dotted) == (folder.as_posix(), None)
    assert backup_job.check_dest("//" + folder.as_posix().lstrip("/")) == (folder.as_posix(), None)
    for bad in ("nas", "./nas", "/", "//", "/mnt/../etc"):
        dest, why = backup_job.check_dest(bad)
        assert dest is None and why, bad


# --- #860: קובץ הסטטוס של הגיבוי — 0600 ----------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes; the server is Linux")
def test_the_backup_status_file_is_0600(tmp_path):
    from datetime import datetime, timezone
    from server.db import connect
    from server.system import backup_job
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    connect(data_dir / "imagesrv.db").close()
    backup_job.run(data_dir, str(tmp_path / "missing-dest"), datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert ((data_dir / backup_job.STATUS_NAME).stat().st_mode & 0o777) == 0o600


# --- #662/#666-669/#807: שורת לוג אחת -------------------------------------------------------

def test_one_line_turns_line_breaks_into_spaces():
    from server.core.logtext import one_line
    assert one_line("aa\r\nINJECTED: x\nmore") == "aa  INJECTED: x more"
    assert one_line(None) == "None"


def test_a_wake_log_line_cannot_be_split_by_the_value(caplog):
    from server.net import wol
    caplog.set_level("ERROR", logger="imagesrv.wol")
    wol._try_send("aa:bb\nINJECTED fake entry", lambda p: None)
    messages = [r.getMessage() for r in caplog.records]
    assert messages and not any("\n" in m for m in messages), messages


def test_a_server_io_log_line_cannot_be_split_by_the_task_id(tmp_path, caplog):
    from server.learn import server_io
    caplog.set_level("ERROR", logger=server_io.log.name)
    conn = sqlite3.connect(tmp_path / "closed.db")
    conn.close()                                  # כל כתיבה נכשלת — ה-except כותב ללוג
    server_io.begin(conn, "capture", "tsk_1\nINJECTED fake entry", tmp_path)
    messages = [r.getMessage() for r in caplog.records]
    assert messages and not any(re.search(r"\n", m) for m in messages), messages
