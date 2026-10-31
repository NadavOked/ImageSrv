"""Line-oriented client used by the native first-boot installer GUI."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from server.console.console_tls import fingerprint_sha256

API_BASE = "https://127.0.0.1:8081/api/wizard"
STATUS_FILE = Path("/etc/imagesrv/firstboot.status")
ROLE_FILE = Path("/etc/imagesrv/installer-role")
CERT_FILE = Path("/var/lib/imagesrv/console-tls/console.crt")
TIMEOUT = 10


def _input() -> dict:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON input: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def _request(path: str, body: dict | None = None) -> dict:
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(
        f"{API_BASE}/{path}", data=raw,
        headers={"Content-Type": "application/json"} if raw is not None else {},
    )
    # Loopback only: this temporary self-signed identity is displayed locally.
    import ssl
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
            status = response.status
            payload = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read()  # Validation errors are data, not bridge failures.
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("wizard response is not an object")
    if not 200 <= status < 300:
        value["_http_status"] = status
    return value


def _fact(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _safe(value: object) -> str:
    return str(value if value is not None else "").replace("\r", " ").replace("\n", " ")


def _emit(key: str, value: object) -> None:
    print(f"{key}={_safe(value)}")


def _flat(value: dict) -> None:
    for key, item in value.items():
        if key == "output":
            for line in str(item or "").splitlines():
                _emit("output", line)
        elif isinstance(item, dict):
            for child, child_value in item.items():
                _emit(f"{key}.{child}", child_value)
        elif isinstance(item, list):
            for child in item:
                _emit(key, child)
        else:
            _emit(key, item)


def _interfaces() -> None:
    value = _request("state")
    defaults = value.pop("defaults", {})
    interfaces = value.pop("interfaces", [])
    _flat(value)
    for nic in interfaces:
        fields = (
            nic.get("name", ""), nic.get("model", ""), nic.get("mac", ""),
            nic.get("link_label", ""), nic.get("current_ip", ""),
            nic.get("source", ""), nic.get("address", ""),
            nic.get("netmask", ""), nic.get("gateway", ""), nic.get("dns", ""),
        )
        _emit("nic", "|".join(_safe(field).replace("|", " ") for field in fields))
    _flat(defaults)


def _finish() -> None:
    status = _fact(STATUS_FILE)
    role = _fact(ROLE_FILE)
    state = status.get("state", "unknown")
    if state == "done" and not CERT_FILE.is_file():
        state = "check-error"
        status["message"] = "טביעת תעודת הקונסולה אינה זמינה אחרי ההתקנה."
    _emit("state", state)
    _emit("role", status.get("role", role.get("role", "standalone")))
    if status.get("message"):
        _emit("error", status["message"])
    if CERT_FILE.is_file():
        _emit("fingerprint", fingerprint_sha256(CERT_FILE.read_bytes()))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in {
        "interfaces", "validate", "apply", "progress", "check-primary", "finish",
    }:
        print("usage: python3 -m server.wizard.bridge ACTION", file=sys.stderr)
        return 2
    action = args[0]
    try:
        if action == "interfaces":
            _input()
            _interfaces()
        elif action == "finish":
            _input()
            _finish()
        elif action == "progress":
            _input()
            progress = _request("progress")
            if progress.pop("_http_status", None) == 404:
                _finish()  # 8081 has already handed over to the real console.
            else:
                _flat(progress)
        elif action == "check-primary":
            body = _input()
            _flat(_request("check-primary", {"primary_url": body.get("primary_url", "")}))
        else:
            _flat(_request(action, _input()))
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"wizard bridge: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
