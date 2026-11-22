"""#3842: גל ישיר שנכשל משחרר את מנוע החתיכות מיד, וגל חדש אינו נדחה.

`SessionStore.fail` אינו קורא ל-`on_closed`. בלי שחרור מפורש בנתיב הכשל הישיר
השולח חי עד "אף מחשב לא התייצב" (180 ש'), ו-`ChunkEngine.start` מסרב.

#3173: הגל הישיר הוא גל של מנוע החתיכות מההתחלה (#3161), ולכן השולח כאן הוא ה-thread
האמיתי של הגל, שממתין שהמשכפלים יתייצבו — לא שולח מדומה.
"""

from fakesend import wait_for
from test_direct_live_receivers import CLONERS, _fail, _running_three
from test_direct_server import _build_hello
from test_server_room import room_server  # noqa: F401


def test_failed_direct_wave_frees_the_chunk_engine_for_the_next_wave(room_server):
    opened = _running_three(room_server)
    wave_id = opened["wave_session_id"]
    chunk = room_server["ctx"].chunk
    # השולח החי של הגל: ממתין למחשב שיתייצב (אף אחד לא אמר `listening`)
    assert wait_for(lambda: (chunk.status() or {}).get("session_id") == wave_id), chunk.status()
    sender = chunk._thread
    assert sender is not None and sender.is_alive()
    chunk._run = lambda session: None            # גל חדש לא משדר באמת; רק הסירוב נבדק
    assert chunk.start({"id": "ses_next", "image_id": "img_7f3a91"}) is not None   # החריץ תפוס

    for mac in CLONERS:
        _fail(room_server, opened, mac)
    assert _build_hello(room_server["anon"])["task"]["direct"]["session_state"] == "failed"

    sender.join(2)
    assert not sender.is_alive(), "השולח של הגל שנכשל נשאר חי"
    assert chunk.start({"id": "ses_next", "image_id": "img_7f3a91"}) is None
