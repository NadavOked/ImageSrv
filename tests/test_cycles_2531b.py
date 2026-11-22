"""#2531 (the rest, second batch) -- the library, DHCP and backup cycles that could move without a contract change.

* `library.images` <-> `library.minfree` / `library.imagefit`: the size arithmetic (`GPT_TAIL_BYTES`,
  `_whole`, `layout_end_bytes`, `required_bytes`) is in `library.imagegeom`; `images` re-exports it.
* `console.dhcp` <-> `net.deploy_net`: `load_configs` / `proxy_configs` are in `net.dhcp_configs`;
  `console.dhcp` re-exports them.
* `system.backup` -> `storage.locations`: `SECRET_KEYS` is in `storage.secret_keys` (a leaf).

The test reads every import, including the ones inside functions.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "server"

PAIRS = [("library.images", "library.minfree"), ("library.images", "library.imagefit"),
         ("console.dhcp", "net.deploy_net"), ("system.backup", "storage.locations")]


def _file(module: str) -> Path:
    flat = SERVER / (module.replace(".", "/") + ".py")
    return flat if flat.exists() else SERVER / module.replace(".", "/") / "__init__.py"


def _imports(module: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(_file(module).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name.removeprefix("server.") for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            base = node.module.removeprefix("server.")
            found.add(base)
            found.update(f"{base}.{a.name}" for a in node.names)
    return found


@pytest.mark.parametrize("a, b", PAIRS)
def test_the_pair_no_longer_imports_each_other_at_any_depth(a, b):
    assert b not in _imports(a) or a not in _imports(b), f"{a} and {b} import each other"


def test_the_leaf_modules_import_nothing_that_could_close_a_cycle():
    assert not {m for m in _imports("library.imagegeom") if m.startswith(("library", "console", "net", "storage"))}
    assert _imports("storage.secret_keys") <= {"__future__", "__future__.annotations"}   # a from-import adds base.name too
    assert not {m for m in _imports("net.dhcp_configs") if m.startswith(("console", "library", "storage"))}


def test_the_size_arithmetic_is_the_same_objects_under_both_names():
    from server.library import images, imagefit, imagegeom, minfree
    for name in ("GPT_TAIL_BYTES", "_whole", "layout_end_bytes", "required_bytes"):
        assert getattr(images, name) is getattr(imagegeom, name)
    assert imagefit.required_bytes is imagegeom.required_bytes and minfree.required_bytes is imagegeom.required_bytes
    manifest = {"sector_size": 512, "partitions": [{"start_sector": 2048, "size_bytes": 1_000_000}]}
    assert images.required_bytes(manifest) == 2048 * 512 + 1_000_000 + 256 * 1024
    assert images.required_bytes({"partitions": [{"start_sector": 1}]}) is None, "geometry missing is not 'small'"


def test_the_dhcp_readers_are_the_same_functions_under_both_names(monkeypatch):
    from server.console import dhcp as console_dhcp
    from server.core import capabilities
    from server.net import dhcp_configs
    assert console_dhcp.load_configs is dhcp_configs.load_configs and console_dhcp.proxy_configs is dhcp_configs.proxy_configs
    monkeypatch.setattr(capabilities, "classrooms", lambda: False)
    assert dhcp_configs.proxy_configs(["x"]) == []
    monkeypatch.setattr(capabilities, "classrooms", lambda: True)
    assert dhcp_configs.proxy_configs(["x"]) == ["x"]


def test_the_secret_keys_are_one_tuple_under_both_names():
    from server.storage import locations, secret_keys
    assert locations.SECRET_KEYS is secret_keys.SECRET_KEYS == ("secret", "chap_secret")
