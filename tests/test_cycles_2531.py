"""#2531 -- five small import cycles are gone (CodeQL `py/cyclic-import`).

Each pair imported each other, one side at the top and one inside a function (the function-level
import "hid" it from Python and not from CodeQL). The shared piece now lives in the lower module:

* `db.KEEP_IPS` (was in `learn.net_history`, which imports `db`)
* `disks.model_slots.clean_name` / `NAME_MAX` (re-exported by `disks.slot_names`)
* `learn.wol_sweep.sweep` (was `learn.wol.sweep`, which needed `net.wol`, which imports `learn.wol`)
* `learn.boot.STEP_LABELS` (re-exported by `agent.boottrace`)
* `rounds.session_kinds` (MULTICAST/UNICAST/KINDS; re-exported by `rounds.sessions`)

The test reads every import, including the ones inside functions, and asserts that no pair imports
each other any more. The behaviours that moved are checked by name.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "server"

PAIRS = [("db", "learn.net_history"), ("disks.model_slots", "disks.slot_names"),
         ("learn.wol", "net.wol"), ("agent.boottrace", "learn.boot"),
         ("rounds.sessions", "rounds.session_view")]


def _file(module: str) -> Path:
    flat = SERVER / (module.replace(".", "/") + ".py")
    return flat if flat.exists() else SERVER / module.replace(".", "/") / "__init__.py"


def _imports(module: str) -> set[str]:
    """Every module this one imports, at any depth (functions included), as `a.b` names under `server`."""
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


def test_the_keep_limit_lives_in_db_and_prunes_the_history_to_it():
    from server import db
    from server.learn import net_history
    assert db.KEEP_IPS == 50 and not hasattr(net_history, "KEEP_IPS")


def test_the_slot_name_rules_are_one_object_in_two_names():
    from server.disks import model_slots, slot_names
    assert slot_names.clean_name is model_slots.clean_name and slot_names.NAME_MAX == model_slots.NAME_MAX == 24
    assert slot_names.clean_name("  כונן 1 ") == "כונן 1"
    with pytest.raises(ValueError):
        slot_names.clean_name("x" * 25)


def test_the_wol_sweep_moved_and_the_learning_module_no_longer_has_it():
    from server.learn import wol as learn_wol
    from server.learn import wol_sweep
    assert callable(wol_sweep.sweep) and not hasattr(learn_wol, "sweep")


def test_the_step_labels_are_one_dict_in_two_modules():
    from server.agent import boottrace
    from server.learn import boot as learn_boot
    assert boottrace.STEP_LABELS is learn_boot.STEP_LABELS and "local-no-disk" in boottrace.STEP_LABELS


def test_the_session_kinds_are_the_same_in_every_name():
    from server.rounds import session_kinds, session_view, sessions
    assert sessions.MULTICAST == session_view.MULTICAST == session_kinds.MULTICAST == "multicast"
    assert sessions.KINDS == session_kinds.KINDS == ("multicast", "unicast")
