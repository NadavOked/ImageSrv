"""A class that defines the same method twice keeps only the last one, silently.

21/11 (metal, integ/r49 d7ab61bbb): #3978 added `ChunkEngine._live(w) -> dict` while the older
`_live(w) -> list` stayed further down the same class. The later one won, and `/api/console/room`
answered 500 whenever a chunk wave ran. ruff F811 does not flag it (the first one is "used"), so
this walks every class in the shipped Python and refuses a second definition of a name.
Property setters/deleters and typing.overload are the legitimate repeats.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCOPES = ("server", "tools", "agent", "installer", "boot")


def _repeats(tree):
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        seen = {}
        for node in cls.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decos = [ast.unparse(d) for d in node.decorator_list]
            if any(d.endswith((".setter", ".deleter")) or d.split(".")[-1] == "overload" for d in decos):
                continue
            if node.name in seen:
                yield f"{cls.name}.{node.name}: line {seen[node.name]} and line {node.lineno}"
            else:
                seen[node.name] = node.lineno


def test_no_class_defines_a_method_twice():
    files = [p for s in SCOPES for p in (ROOT / s).glob("**/*.py")]
    assert len(files) > 100, f"only {len(files)} files found -- the scan did not run"
    bad = []
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        bad += [f"{p.relative_to(ROOT)}: {r}" for r in _repeats(tree)]
    assert not bad, "a later definition silently replaces the earlier one:\n" + "\n".join(bad)


def test_the_scan_catches_a_repeat():
    src = "class A:\n    def f(self): return 1\n    def f(self): return []\n"
    assert list(_repeats(ast.parse(src))) == ["A.f: line 2 and line 3"]
