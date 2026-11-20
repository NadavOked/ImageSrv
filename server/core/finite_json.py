"""Decode JSON whose numbers can be served in a JSON response."""

import json
import math


def _no_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON constant {name}")


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def loads(raw: str | bytes) -> object:
    return json.loads(raw, parse_constant=_no_constant, parse_float=_finite)
