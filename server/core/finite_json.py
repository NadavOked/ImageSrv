"""Decode JSON whose numbers can be served in a JSON response."""

import json
import math
from typing import Any


def _no_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON constant {name}")


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def loads(raw: str | bytes) -> Any:
    """Like ``json.loads`` (whose return type is ``Any`` too): what comes back is whatever the
    text said, and the caller validates its shape (``ImageLibrary._validate``, ``isinstance``)."""
    return json.loads(raw, parse_constant=_no_constant, parse_float=_finite)
