"""#2750: the agent port's `async def` routes do no sqlite work on the event loop.

An `async def` handler runs on the loop itself. sqlite that waits for a write
lock waits there too -- up to `busy_timeout` (5s) -- and nothing else on the
server is served meanwhile: not hello, not the console, not the streams (#2748).
The shape that is safe: the body is read on the loop, and the DB work runs in
`run_in_threadpool` (hello, progress, #2749), or the handler is a plain `def`.

Two tests:
- A structural scan (AST) over the agent-port route modules: an `async def`
  route that touches the DB -- directly or through a helper in the same module
  -- outside a `run_in_threadpool(...)` call fails here, by name.
- A behaviour test like `test_progress_async_2748`: a disk event while another
  connection holds the write lock does not delay a console `/me`.

"Touches the DB", for the scan, is an over-approximation that stays readable:
a reference to `.conn`, or a call that hands the server context (`ctx`) to
someone -- every DB helper on these routes takes `ctx` or `ctx.conn`. Helpers
defined in the same module (closures included) are followed transitively.
`ctx.library.root` and other attributes of `ctx` are not "handing `ctx`".
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted((ROOT / "server" / "agent").glob("*.py")) + [
    ROOT / "server" / "rounds" / "cloner_round.py",
    ROOT / "server" / "rounds" / "station.py",
    ROOT / "server" / "rounds" / "direct.py",
    ROOT / "server" / "library" / "capture.py",
]
ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "api_route", "websocket"}

#: Routes the scan may not flag. Each one is named with the reason; an entry that
#: is no longer flagged fails `test_the_exemptions_are_still_needed`, so the list
#: cannot rot into a blanket pass.
EXEMPT: dict[tuple[str, str], str] = {
    ("server/library/capture.py", "create_capture"):
        "console route (8081, admin_only), not the agent port -- #2750 part B",
}


def _is_route(fn: ast.AsyncFunctionDef) -> bool:
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in ROUTE_METHODS):
            return True
    return False


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _is_threadpool(call: ast.Call) -> bool:
    f = call.func
    return ((isinstance(f, ast.Name) and f.id == "run_in_threadpool")
            or (isinstance(f, ast.Attribute) and f.attr == "run_in_threadpool"))


def _own_nodes(fn):
    """Every node in `fn`'s own body: not inside nested defs/lambdas, and not
    inside the arguments of a `run_in_threadpool(...)` call (those run off the loop)."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call) and _is_threadpool(node):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _direct_db(fn) -> str | None:
    """The first sign of DB work in `fn`'s own body, as text -- or None."""
    for node in _own_nodes(fn):
        if isinstance(node, ast.Attribute) and node.attr == "conn":
            return f"line {node.lineno}: .conn"
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Name) and arg.id == "ctx":
                    return f"line {node.lineno}: a call is handed ctx"
    return None


def _defs(tree: ast.Module) -> dict[str, ast.AST]:
    """Every function defined anywhere in the module, by name (closures included)."""
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _db_on_loop(fn, defs, seen=None) -> str | None:
    seen = set() if seen is None else seen
    if fn.name in seen:
        return None
    seen.add(fn.name)
    hit = _direct_db(fn)
    if hit:
        return f"{fn.name} {hit}"
    for node in _own_nodes(fn):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in defs:
                hit = _db_on_loop(defs[name], defs, seen)
                if hit:
                    return hit
    return None


def _scan() -> dict[tuple[str, str], str]:
    found = {}
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defs = _defs(tree)
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and _is_route(node):
                hit = _db_on_loop(node, defs)
                if hit:
                    found[(rel, node.name)] = hit
    return found


def test_no_agent_port_async_route_does_sqlite_on_the_event_loop():
    flagged = {k: v for k, v in _scan().items() if k not in EXEMPT}
    assert not flagged, (
        "async def routes that touch the DB on the event loop (#2748/#2750) --"
        " read the body on the loop, run the DB work in run_in_threadpool:\n"
        + "\n".join(f"  {f}::{name}: {why}" for (f, name), why in sorted(flagged.items())))


def test_the_exemptions_are_still_needed():
    found = _scan()
    stale = [k for k in EXEMPT if k not in found]
    assert not stale, f"exempt but no longer flagged -- remove from EXEMPT: {stale}"


def test_the_scan_sees_the_shape_it_guards_against():
    """The detector itself: a route like the pre-#2748 progress handler is flagged,
    the fixed shape is not (otherwise a green scan could mean a blind scan)."""
    src = '''
@router.post("/x")
async def bad(request):
    body = await read(request)
    return helper(body)

def helper(body):
    return ctx.conn.execute("SELECT 1")

@router.post("/y")
async def good(request):
    body = await read(request)
    return await run_in_threadpool(helper, body)

@router.post("/z")
async def gate(request):
    refused = identity_gate(ctx, "m", request, "z")
    return refused
'''
    tree = ast.parse(src)
    defs = _defs(tree)
    routes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    assert _db_on_loop(routes["bad"], defs)
    assert _db_on_loop(routes["good"], defs) is None
    assert _db_on_loop(routes["gate"], defs)
    # and the scan finds real routes at all (a path typo would scan nothing)
    count = sum(1 for p in MODULES for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                if isinstance(n, ast.AsyncFunctionDef) and _is_route(n))
    assert count >= 20, count


# --- behaviour: a disk event on a locked DB does not block another request ---

LOCK_SECONDS = 2.0
MAC = "b4:2e:99:07:1a:c4"           # a machine `setup_classroom` registers


def test_a_disk_event_on_a_locked_db_does_not_block_another_request(server):
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    from conftest import setup_classroom          # noqa: PLC0415
    setup_classroom(server)            # #2487: an unregistered mac does not write
    admin = server["admin"]
    # Warm-up: the session's `touch` writes at most once a minute -- after this
    # call, `/me` below only reads, and reads are not blocked by a writer (WAL).
    assert admin.get("/api/console/me").status_code == 200

    locker = sqlite3.connect(server["ctx"].conn._path, isolation_level=None,
                             check_same_thread=False)
    locker.execute("BEGIN IMMEDIATE")            # the write lock, held by "someone else"
    released = threading.Event()

    def release():
        locker.execute("ROLLBACK")
        released.set()

    timer = threading.Timer(LOCK_SECONDS, release)
    # A valid event of a registered machine: `disk_events.ingest` UPSERTs it --
    # a write that waits for the lock (the gate before it only reads; WAL).
    event = {"session_id": "ses_2750", "mac": MAC, "disk": "sda", "port": 2,
             "smart": {"verdict": "warn", "reason": "reallocated",
                       "realloc": 8, "pending": 0, "uncorrectable": 0, "crc": 0},
             "write_state": "rescue", "decision": "rescue"}

    async def scenario():
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                     cookies=admin.cookies) as client:
            t0 = time.monotonic()

            async def disk_event():
                r = await client.post("/api/v1/agent/disk-event", json=event)
                return r, time.monotonic() - t0

            async def light():
                await asyncio.sleep(0.2)          # the event is already waiting on the lock
                r = await client.get("/api/console/me")
                return r, time.monotonic() - t0

            return await asyncio.gather(disk_event(), light())

    timer.start()
    try:
        (ev, ev_at), (me, me_at) = asyncio.run(scenario())
    finally:
        timer.cancel()
        if not released.is_set():
            release()
        locker.close()

    assert me.status_code == 200, me.text
    assert me_at < LOCK_SECONDS - 0.5, (
        f"/me finished {me_at:.2f}s after the lock was taken; the lock was held"
        f" {LOCK_SECONDS}s -- the disk event blocked the event loop")
    # The event itself waited for the lock and then answered as before.
    assert ev.status_code == 200 and ev.json()["ok"] is True, ev.text
    assert ev_at >= LOCK_SECONDS - 0.1, ev_at
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM disk_events WHERE session_id = 'ses_2750' AND mac = ?",
        (MAC,)).fetchone()["n"] == 1
