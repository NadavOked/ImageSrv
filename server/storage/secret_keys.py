"""The `params_json` keys of a storage location that hold a secret (#2460).

A leaf module (#2531): `system/backup.py` strips them from every DB backup, and importing
`storage.locations` for a two-element tuple closed an import cycle through the backup modules.
`storage.locations.SECRET_KEYS` is the same tuple.
"""
from __future__ import annotations

SECRET_KEYS = ("secret", "chap_secret")
