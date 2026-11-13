"""#2373 — העברה בין אתרים שנקטעה ב-restart אינה נשארת "רצה" לנצח.

ההעברה רצה בתהליכון בתוך תהליך השרת; אחרי עלייה אין תהליכון לאף שורה.
עד כאן `reconcile_on_start` לא נגע ב-`storage_transfers`, ושורה `sending`
נשארה כך — ומאז #1891 היא גם חוסמת את "החל עדכון" ואת האתחול המתוזמן.

כמו קליטה וסבב (#411): עלייה מחדש = שרת **שני** על אותו `--data-dir`.
ההבחנה שנבדקת: `failed` **בשם** (לא מחיקה, לא `done`), `bytes_sent` נשמר,
שורה שכבר הסתיימה לא נוגעים בה, ויומן.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from test_reconcile_on_start import _boot, _events  # noqa: E402

from server import auto_reboot  # noqa: E402

try:
    from server.rounds.reconcile import RESTARTED_TRANSFER
except ImportError:   # בקרה שלילית: נופלים על ההתנהגות, לא על ImportError
    RESTARTED_TRANSFER = "נקטעה בהפעלה מחדש של השרת"

ROWS = (("t_q", "queued", "push", None), ("t_s", "sending", "push", None),
        ("t_v", "verifying", "push", None), ("t_p", "sending", "pull", None),
        ("t_d", "done", "push", None), ("t_f", "failed", "push", "המשני סירב"))


def _seed(conn) -> None:
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
        " VALUES ('n1', 'חיפה', 'https://10.0.0.2:8443', 'x', '2026-11-04T00:00:00+00:00')")
    for tid, state, direction, error in ROWS:
        conn.execute(
            "INSERT INTO storage_transfers (id, node_id, image_id, image_name, state,"
            " bytes_sent, bytes_total, error, started_by, created_at, updated_at, direction)"
            " VALUES (?, 'n1', ?, 'x', ?, 40, 100, ?, 'noc', 'x', 'x', ?)",
            (tid, f"img_{tid}", state, error, direction))
    conn.commit()


def _rows(conn) -> dict:
    return {r["id"]: r for r in conn.execute(
        "SELECT id, state, error, bytes_sent FROM storage_transfers")}


def test_a_transfer_cut_by_a_restart_fails_by_name_and_no_longer_blocks(tmp_path, monkeypatch):
    first = _boot(tmp_path, monkeypatch, seed=True)
    _seed(first["ctx"].conn)
    assert "העברת אימג' בין אתרים" in auto_reboot.busy_reason(
        SimpleNamespace(conn=first["ctx"].conn), include_update=False)   # לפני: חוסם
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    conn = second["ctx"].conn
    rows = _rows(conn)
    for tid in ("t_q", "t_s", "t_v", "t_p"):
        assert rows[tid]["state"] == "failed", (tid, dict(rows[tid]))
        assert rows[tid]["error"] == RESTARTED_TRANSFER
        assert rows[tid]["bytes_sent"] == 40          # מה שנשלח עד הנפילה — לא נמחק
    assert rows["t_d"]["state"] == "done" and rows["t_d"]["error"] is None
    assert rows["t_f"]["state"] == "failed" and rows["t_f"]["error"] == "המשני סירב"
    assert _events(second["ctx"]).count("storage_transfer_failed") == 4
    assert auto_reboot.busy_reason(SimpleNamespace(conn=conn), include_update=False) is None
    second["ctx"].sender.stop()


def test_reconcile_reports_the_transfers_it_failed(tmp_path):
    from server.db import connect
    from server.rounds.reconcile import reconcile_on_start

    conn = connect(tmp_path / "x.db")
    _seed(conn)
    out = reconcile_on_start(conn, tmp_path / "images")
    assert sorted(out["transfers_failed"]) == ["t_p", "t_q", "t_s", "t_v"]
    assert reconcile_on_start(conn, tmp_path / "images")["transfers_failed"] == []
