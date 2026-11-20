"""#3181: שני `chunkcast recv` על אותה קבוצת מולטיקאסט ואותו פורט מקבלים שניהם כל חבילה.

זו ההנחה שההצטרפות המיידית נשענת עליה (`agent/lib/restore/drawerfill.sh`, `_fl_live`): מגירה שחזרה באמצע מעבר
מאזינה **ליד** המקלט של הסבב, במקלט משלה ועם קובץ מצב משלה. `chunkcast` פותח את ה-socket עם `SO_REUSEADDR` וקושר
אותו לכתובת הקבוצה; בלינוקס כל socket כזה מקבל עותק של כל חבילת מולטיקאסט. כאן זה נמדד עם הבינארי האמיתי, ולא
נקרא מהקוד: שולח אחד, שני מקלטים, קבוצה אמיתית עם TTL 0 (החבילות אינן יוצאות מהמכונה).

**זה שומר על הנחה, לא על שינוי:** `chunkcast.c` לא שונה ב-#3181, והטסט עובר גם על הבסיס. אם הוא נופל, ההצטרפות
המיידית אינה עובדת על המכונה הזו, ושום טסט עם `chunkcast` מזויף לא היה אומר את זה.
"""

from __future__ import annotations

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("chunkcast-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("img"), cc / "fanout", noisy=(0, 2))


def test_a_second_receiver_on_the_same_group_takes_the_pass_too_and_keeps_what_it_held(cc, img, tmp_path):
    port, session = C.free_port(), C.new_session()
    group = f"239.255.{port >> 8 & 255}.{port & 255 or 1}:{port}"
    a, b = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk")
    a_state, b_state = tmp_path / "a.state", tmp_path / "b.state"

    # the drawer before it was pulled: chunks 0-1 are durable on it
    before = C.recv(cc, img, [b], group, session, b_state, tag=tmp_path / "before")
    s = C.send(cc, img, group, session, tag=tmp_path / "send0", chunks="0-1", extra=("--ttl", "0"))
    assert s.wait() == 0, s.log()
    assert before.wait() == 0, before.log()
    assert C.state_of(b_state)[str(b)]["durable"] == {0, 1}

    # the round's receiver, and beside it the drawer's own: the same group, the same port, the same pass
    ra = C.recv(cc, img, [a], group, session, a_state, tag=tmp_path / "ra")
    rb = C.recv(cc, img, [b], group, session, b_state, tag=tmp_path / "rb")
    s = C.send(cc, img, group, session, tag=tmp_path / "send1", extra=("--ttl", "0"))
    assert s.wait() == 0, s.log()
    assert ra.wait() == 0, ra.log()
    assert rb.wait() == 0, rb.log()
    want = img.expected()
    assert a.read_bytes() == want and b.read_bytes() == want
    whole = {"alive": True, "durable": set(range(img.n)), "reason": ""}
    assert C.state_of(a_state) == {str(a): whole} and C.state_of(b_state) == {str(b): whole}
