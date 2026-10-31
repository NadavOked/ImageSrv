"""Re-verify stored image partition files against their manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from server.db import _settle, _write_lock, now_iso, writing
from server.library.images import (ImageLibrary, guarded_image_path, streamed_partitions,
                                   valid_image_id)

CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _scrub_image(manifest: dict) -> dict:
    # #1215: a location that is not reachable left only its cached manifest --
    # no folder to hash. "Could not check" is its own state, never "intact".
    if not manifest.get("_available", True) or not manifest.get("_dir"):
        return {"id": manifest["id"], "state": "unavailable", "files": []}
    files = []
    # Each image is checked where it actually lives, on whichever location
    # holds it, and a manifest file name may not step out of that folder --
    # the same boundary the upload gate (archive.py) applied when it entered.
    folder = Path(manifest["_dir"])
    for part in streamed_partitions(manifest):
        expected = part["sha256"]
        path = guarded_image_path(folder, part["file"])
        result = {
            "index": part["index"],
            "file": part["file"],
            "expected_sha256": expected,
        }
        try:
            if path is None:
                result["state"] = "missing"
            else:
                actual = _sha256(path)
                result["actual_sha256"] = actual
                result["state"] = "ok" if actual == expected else "mismatch"
        except OSError as exc:
            result["state"] = "unreadable"
            result["error"] = str(exc)
        files.append(result)
    return {
        "id": manifest["id"],
        "state": "intact" if all(item["state"] == "ok" for item in files) else "drift",
        "files": files,
    }


def scrub_library(library: ImageLibrary, image_id: str | None = None, conn=None) -> dict:
    """Hash one stored image, or every image when ``image_id`` is omitted.

    ``library`` is the console's own library, so every storage location it
    knows is covered (#1215), not only ``--images``.

    With ``conn``, each image's ``{ts, state}`` is saved (#972) so the
    console shows the last result after the browser session ends. An
    ``unavailable`` image is not saved: it is not a verification result, and
    it must not overwrite the last real one (a ``drift`` above all)."""
    if image_id is not None and not valid_image_id(image_id):
        raise ValueError("מזהה אימג' לא תקין")
    manifests = library.scan()
    if image_id is not None:
        manifest = manifests.get(image_id)
        if manifest is None:
            raise KeyError(image_id)
        selected = [manifest]
    else:
        selected = [manifests[key] for key in sorted(manifests)]
    images = [_scrub_image(manifest) for manifest in selected]
    if conn is not None:
        _record(conn, [image for image in images if image["state"] != "unavailable"])
    return {"intact": all(image["state"] == "intact" for image in images), "images": images}


def _record(conn, images: list[dict]) -> None:
    ts = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        conn.executemany(
            "INSERT INTO image_scrubs (image_id, ts, state) VALUES (?, ?, ?)"
            " ON CONFLICT (image_id) DO UPDATE SET ts = excluded.ts, state = excluded.state",
            [(image["id"], ts, image["state"]) for image in images],
        )


def last_scrubs(conn) -> dict[str, dict]:
    """``{image_id: {"ts", "state"}}`` for every image ever scrubbed. An image
    missing here was never re-verified since it entered the library."""
    rows = conn.execute("SELECT image_id, ts, state FROM image_scrubs")
    return {row["image_id"]: {"ts": row["ts"], "state": row["state"]} for row in rows}


def forget_scrub(conn, image_id: str) -> None:
    """A deleted image's result must not attach to files that later reuse its id."""
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM image_scrubs WHERE image_id = ?", (image_id,))
