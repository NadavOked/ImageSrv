"""#3842: גל ישיר שנכשל משחרר את מנוע החתיכות מיד, וגל חדש אינו נדחה.

`SessionStore.fail` אינו קורא ל-`on_closed`. בלי שחרור מפורש בנתיב הכשל הישיר
השולח חי עד "אף מחשב לא התייצב" (180 ש'), ו-`ChunkEngine.start` מסרב.
"""

import threading

from test_direct_live_receivers import CLONERS, _fail, _running_three
from test_direct_server import _build_hello
from test_server_room import room_server  # noqa: F401

from server.rounds import chunksend


def test_failed_direct_wave_frees_the_chunk_engine_for_the_next_wave(room_server):
    opened = _running_three(room_server)
    wave_id = opened["wave_session_id"]
    chunk = room_server["ctx"].chunk
    chunk._run = lambda session: None            # גל חדש לא משדר באמת; רק הסירוב נבדק
    # שולח חי של הגל, שמחכה לעצירה כמו ה-thread האמיתי שמחכה למחשב שיתייצב
    chunk._wave = chunksend._Wave(wave_id, "img_7f3a91", chunk.key_for(wave_id))
    chunk._thread = threading.Thread(target=lambda: chunk._stop.wait(10), daemon=True)
    chunk._thread.start()
    assert chunk.start({"id": "ses_next", "image_id": "img_7f3a91"}) is not None   # החריץ תפוס

    for mac in CLONERS:
        _fail(room_server, opened, mac)
    assert _build_hello(room_server["anon"])["task"]["direct"]["session_state"] == "failed"

    chunk._thread.join(2)
    assert not chunk._thread.is_alive(), "השולח של הגל שנכשל נשאר חי"
    assert chunk.start({"id": "ses_next", "image_id": "img_7f3a91"}) is None
