"""Version of the running server, read once, including an unknown result.

The station polls every two seconds. A source checkout changing underneath
this process does not change its version until the server restarts.
"""
from functools import lru_cache
from server.paths import REPO_ROOT

from server.update import current_version, default_hooks


@lru_cache(maxsize=1)
def server_version() -> str | None:
    return current_version(default_hooks(), REPO_ROOT)
