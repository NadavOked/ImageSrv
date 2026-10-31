"""Repository locations, anchored here so callers can move into subpackages."""

from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parent
STATIC_DIR = SERVER_DIR / "static"
AGENT_LIB = REPO_ROOT / "agent" / "lib"
AGENT_LIB_DATA = AGENT_LIB / "data"
