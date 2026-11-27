"""Console source tests must follow the script order in index.html."""

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ALLOWLIST = {
    "console_src.py",  # Implements the Python console source loader.
    "console_src.cjs",  # Implements the Node console source loader.
    "memory_line_3320.test.cjs",  # Prose names the origin of the VM stubs.
    "test_brain_services_3457.py",  # Prose lists files for the negative control.
    "test_codeql_1535.py",  # Prose identifies where stripTags comes from.
    "test_console_static_pages_4041.py",  # Checks script tags and the HTTP static mount.
    "test_deploy_nic_1810.py",  # Prose identifies the console function involved.
    "test_dev_tools_3109.py",  # Defines a synthetic script-tag fixture.
    "test_disk_rule_parity_2848.py",  # Declares an unused path without reading it.
    "test_login_mfa.py",  # Prose describes how the console inserts the QR code.
    "test_missing_drawer_server_2201.py",  # Prose lists files for the negative control.
    "test_publish_to_public.py",  # Writes a synthetic file in a fixture repo.
    "test_sec_console_2471_2493_2817.py",  # Checks HTTP security headers and content types.
    "test_update.py",  # Prose describes the console's HTTP request.
}
NODE_READ = re.compile(
    r"""\b(?:read(?:File(?:Sync)?)?|open(?:Sync)?|createReadStream)\s*\(\s*"""
    r"""(?:(?:path\.(?:join|resolve)|new URL)\s*\([^();]*?[,\s])?"""
    r"""(['"`])(?:[^'"`\n]*[/\\])?console\.js\1"""
)


def _python_reads_console(source):
    nodes = list(ast.walk(ast.parse(source)))
    paths = set()

    def is_console(node):
        return any(
            (isinstance(part, ast.Constant) and isinstance(part.value, str)
             and part.value.replace("\\", "/").rsplit("/", 1)[-1] == "console.js"
             and not part.value.startswith("/console/"))
            or (isinstance(part, ast.Name) and part.id in paths)
            for part in ast.walk(node)
        )

    for node in nodes:
        if isinstance(node, ast.Assign) and is_console(node.value):
            paths.update(target.id for target in node.targets if isinstance(target, ast.Name))

    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"open", "read_text", "read_bytes"}:
            if is_console(func.value):
                return True
        if node.args and (
            isinstance(func, ast.Name) and func.id == "open"
            or isinstance(func, ast.Attribute) and func.attr == "open"
        ) and is_console(node.args[0]):
            return True
    return False


def test_console_source_reads_use_the_helper():
    offenders = []
    for path in sorted(TESTS.rglob("*")):
        name = path.relative_to(TESTS).as_posix()
        if path.suffix not in {".py", ".cjs"} or name in ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        if NODE_READ.search(source) or (path.suffix == ".py" and _python_reads_console(source)):
            offenders.append(name)
    assert not offenders, "Read console source with console_src in:\n" + "\n".join(offenders)
