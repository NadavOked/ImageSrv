from pathlib import Path

from server.db import connect, set_setting
from server.net import igmp_querier


def test_admin_guide_matches_querier_default(tmp_path):
    guide = Path(__file__).resolve().parents[1] / "docs/guides/admin-guide-he.md"
    section = guide.read_text(encoding="utf-8").split("### 4.4 ", 1)[1].split("### 4.5 ", 1)[0]
    conn = connect(tmp_path / "querier.db")
    try:
        documented_default = "דלוק" if igmp_querier.enabled(conn) else "כבוי"
        assert f"`{igmp_querier.SETTING}` {documented_default} כברירת מחדל" in section
        set_setting(conn, igmp_querier.SETTING, "false")
        assert not igmp_querier.enabled(conn)
        assert f"`{igmp_querier.SETTING}=false`" in section
    finally:
        conn.close()
