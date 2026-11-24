"""חיבור ה-SQL כפי שקוד היומן והכתיבה רואים אותו -- הטיפוס בלבד, בלי ייבוא של `server.db`.

`room_state` הוא חישובי מצב טהורים, וטסט (#2594) מבטיח שייבואו אינו טוען את `server.db`; לכן הטיפוס יושב כאן,
`server.db` מייבא אותו ומחזיר אותו בשם (`from server.db import SqlConnection` ממשיך לעבוד).
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping, Protocol, Sequence


class SqlConnection(Protocol):
    """מה שקוד היומן והכתיבה דורשים מ-``conn``: חיבור ``sqlite3`` גולמי, או ``Database`` (שמתחזה לו --
    ראו שם). ההבדל היחיד מ-``sqlite3.Connection`` הוא שאין צורך להיות הוא."""

    def execute(self, sql: str, parameters: Sequence[object] | Mapping[str, object] = ..., /) -> sqlite3.Cursor: ...
    def executemany(self, sql: str, seq_of_parameters: Iterable[Sequence[object] | Mapping[str, object]], /) -> sqlite3.Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    @property
    def in_transaction(self) -> bool: ...
