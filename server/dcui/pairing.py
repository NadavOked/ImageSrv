"""F4 on a secondary: the one-time pairing code, by physical presence.

Nadav 16/10 (variant A of the mockup, PR #1525): the server screen shows the
code with **no password** -- being at the server's keyboard is the
authorization, as for F9.  The risk shown to him and accepted: the code alone
is not enough.  It is single-use, expires after 10 minutes, allows 5 wrong
tries, and works only when an admin signed in to the primary's console types
it there (after comparing the fingerprint this screen shows).

The channel is this process itself: root on tty1, straight to the DB through
:mod:`server.interserver.storage_nodes`, like every other action of the screen.  There is
no route and no network address for any of it -- the loopback API
(``/api/console/storage-pairing-window``) is untouched and still needs an
admin session.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from server.interserver import interserver_auth, storage_nodes
from server.db import journal

from .actions import ActionResult


@dataclass(frozen=True)
class Code:
    """What the code screen shows.  ``code`` is the only copy of the plain
    code: the DB keeps its salted hash, and no status call returns it."""
    code: str
    window: str                     # storage_nodes.pairing_window_id -- not secret
    expires: float                  # epoch seconds
    node_id: str                    # sn_<16 hex>, "" when there is no identity yet
    spki: str                       # 64 hex, "" when there is no identity yet
    advertised: str                 # https://host:port, "" when not set


def _epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    when = datetime.fromisoformat(iso)
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def has_parent(conn) -> bool:
    return storage_nodes.has_parent(conn)


def open_code(conn) -> tuple[ActionResult, Code | None]:
    """A new window (replacing an open one); the code comes back once."""
    if conn is None:
        return ActionResult(False, "The server database is not available."), None
    try:
        opened = storage_nodes.open_physical_pairing(conn)
        identity = storage_nodes.identity_display(conn) or ("", "")
        advertised = storage_nodes.advertised_url(conn)
    except storage_nodes.NotSecondaryError:
        return ActionResult(False, "Pairing codes are shown only on a secondary server."), None
    except storage_nodes.ParentAlreadyEnrolledError:
        return ActionResult(False, "This server is already paired; disconnect it first."), None
    except (sqlite3.Error, ValueError):
        return ActionResult(False, "The pairing window could not be opened."), None
    return ActionResult(True, "Pairing code opened."), Code(
        opened["code"], opened["window_id"], _epoch(opened["expires_at"]),
        identity[0], identity[1], advertised)


def state(conn, code: Code, now: float) -> str:
    """``open`` | ``paired`` | ``expired`` | ``closed`` (gone before its time:
    5 wrong tries on the primary, or another window replaced it) |
    ``unchecked`` (the DB could not be read -- not "still open")."""
    try:
        if storage_nodes.has_parent(conn):
            return "paired"
        status = interserver_auth.pairing_window_status(
            conn, now=datetime.fromtimestamp(now, timezone.utc))
        same = storage_nodes.pairing_window_id(conn) == code.window
    except sqlite3.Error:
        return "unchecked"
    if status.get("open") and same:
        return "open"
    return "expired" if now >= code.expires else "closed"


def close_code(conn, code: Code) -> bool:
    """The code screen was left: its code is void (a newer window opened by
    the API is left alone).  ``True`` = the close ran without error (closed,
    or it was not this code's window any more).  ``False`` = it failed: the window
    stays open until it expires on its own, and the screen must say so
    (principle 5) rather than announce a cancelled code."""
    try:
        storage_nodes.close_physical_pairing(conn, code.window)
    except (storage_nodes.NotSecondaryError, sqlite3.Error):
        return False
    return True


def paired_details(conn) -> dict:
    """The primary this server is now paired with, as far as it is recorded."""
    info = storage_nodes.parent_info(conn) or {}
    return {"parent_id": info.get("parent_id") or "",
            "enrolled_at": info.get("enrolled_at") or "",
            "primary_url": storage_nodes.primary_url(conn)}


def disconnect(conn) -> ActionResult:
    """F4 on a paired secondary, after the red button.  Read back."""
    if conn is None:
        return ActionResult(False, "The server database is not available.")
    try:
        storage_nodes.unbind_parent_physical(conn)
        still = storage_nodes.has_parent(conn)
    except storage_nodes.NotSecondaryError:
        return ActionResult(False, "Pairing codes are shown only on a secondary server.")
    except sqlite3.Error:
        return ActionResult(False, "The disconnect could not be written.")
    if still:
        return ActionResult(False, "The primary server is still recorded; the disconnect was not confirmed.")
    return ActionResult(True, "Disconnected from the primary server.")


def advertised(conn) -> str:
    """The saved address the primary sees; "" when unset or unreadable."""
    if conn is None:
        return ""
    try:
        return storage_nodes.advertised_url(conn)
    except sqlite3.Error:
        return ""


def save_advertised(conn, raw: str) -> ActionResult:
    """F2: the address the primary sees.  Validated, saved, read back."""
    if conn is None:
        return ActionResult(False, "The server database is not available.")
    try:
        wanted = storage_nodes.normalize_advertised_url(raw)
    except interserver_auth.InterserverURLError:
        return ActionResult(False, "Invalid address: use host:port, for example 203.0.113.40:8443.")
    try:
        stored = storage_nodes.set_advertised_url(conn, wanted)
    except sqlite3.Error:
        return ActionResult(False, "The address could not be saved.")
    if stored != wanted:
        return ActionResult(False, "The address was not read back as saved.")
    journal(conn, "dcui_advertised_url", stored or "cleared", "dcui")
    if not stored:
        return ActionResult(True, "Address cleared.")
    return ActionResult(True, "Address saved.", (stored,))
