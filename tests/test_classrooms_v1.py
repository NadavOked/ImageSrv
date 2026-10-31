"""‏#1081 — v1 בלי כיתות: דגל `classrooms` קבוע False, הקונסולה והסוכן מסתירים.

הדגל אינו הגדרה למפעיל. v2 מדליק אותו בקוד. **בקרה שלילית:** הדגל True
בלי שינוי הטסטים → הטסטים נופלים על False is True / על מחרוזות כיתה ב-DOM.
"""

from __future__ import annotations

from pathlib import Path

from server.core import capabilities

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"


def test_classrooms_capability_is_hardcoded_false():
    """לא הגדרה למפעיל — קבוע בקוד. v2 מדליק."""
    assert capabilities.CLASSROOMS is False
    assert capabilities.classrooms() is False


def test_me_carries_classrooms_false(server):
    me = server["admin"].get("/api/console/me").json()
    assert me["capabilities"]["classrooms"] is False


def test_hello_carries_classrooms_false_for_a_build_machine(server):
    from test_server_api import hello, register_build_machine  # noqa: PLC0415

    mac = register_build_machine(server)
    body = hello(server, mac)
    assert body["classrooms"] is False


def test_agent_state_carries_classrooms_false(server):
    from test_server_api import register_build_machine  # noqa: PLC0415

    mac = register_build_machine(server)
    state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    assert state["classrooms"] is False


def test_index_html_gates_the_classrooms_tree_node_behind_data_cap():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-cap="classrooms"' in page
    assert "selectMachinesRole('classroom',this)" in page   # ‏#1300: עלה כמו במוקאפ — העלה עצמו מודגש
    # מתחיל מוסתר — כמו סניפים — עד ש-/me אומר true.
    assert 'class="inventory-node hidden" data-cap="classrooms"' in page


def test_monkeypatching_the_flag_on_restores_the_capability(server, monkeypatch):
    """המסלול ש-v2 מדליק: אותו קוד, הדגל True → /me ו-hello ו-/state."""
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    me = server["admin"].get("/api/console/me").json()
    assert me["capabilities"]["classrooms"] is True
    from test_server_api import hello, register_build_machine  # noqa: PLC0415

    mac = register_build_machine(server)
    assert hello(server, mac)["classrooms"] is True
    assert server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()["classrooms"] is True
