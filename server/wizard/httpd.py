"""HTTPS/JSON transport for the first-boot wizard."""

from __future__ import annotations

import json
import mimetypes
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from server.console.console_tls import ensure_console_cert
from server.paths import STATIC_DIR

from .core import WizardConfig, WizardState, check_primary, validate_config

STATIC = Path(__file__).with_name("static")
DESIGN_TOKENS = STATIC_DIR / "design-tokens.css"
#: ‏#1302: ה-favicon של ImageSrv — אותם קבצים שהקונסולה מגישה.
FAVICONS = {f"/{name}": STATIC_DIR / name for name in ("favicon.svg", "favicon.ico")}


class WizardHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address, state: WizardState, nic_fact: Path, role_fact: Path):
        self.wizard = state
        self.nic_fact = nic_fact
        self.role_fact = role_fact
        super().__init__(address, WizardHandler)

    def release_port(self) -> None:
        """Stop accepting new wizard connections before the console binds 8081."""
        self.shutdown()
        self.server_close()


class WizardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ImageSrvWizard/1"

    @property
    def app(self) -> WizardHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args) -> None:
        # No request bodies are logged. In particular, passwords never reach the journal.
        print(f"imagesrv-wizard: {self.address_string()} {fmt % args}")

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if size < 0 or size > 64 * 1024:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "גוף הבקשה גדול מדי."})
            return None
        try:
            value = json.loads(self.rfile.read(size) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "JSON לא תקין."})
            return None
        if not isinstance(value, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "גוף הבקשה חייב להיות אובייקט JSON."})
            return None
        return value

    def _file(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{kind}; charset=utf-8" if kind.startswith(("text/", "application/javascript")) else kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._file(STATIC / "index.html")
        elif path == "/wizard.css":
            self._file(STATIC / "wizard.css")
        elif path == "/wizard.js":
            self._file(STATIC / "wizard.js")
        elif path == "/design-tokens.css":
            self._file(DESIGN_TOKENS)
        elif path in FAVICONS:
            self._file(FAVICONS[path])
        elif path == "/api/wizard/state":
            self._json(HTTPStatus.OK, {"ok": True, **self.app.wizard.initial(self.app.nic_fact, self.app.role_fact)})
        elif path == "/api/wizard/progress":
            self._json(HTTPStatus.OK, {"ok": True, **self.app.wizard.progress()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        body = self._body()
        if body is None:
            return
        if path == "/api/wizard/check-primary":
            result = check_primary(str(body.get("primary_url", "")))
            self._json(HTTPStatus.OK, result)
            return
        if path in {"/api/wizard/validate", "/api/wizard/apply"}:
            if path.endswith("/apply"):
                active_job = self.app.wizard.active_job()
                if active_job:
                    self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": active_job})
                    return
            config = WizardConfig.from_json(body)
            names = {row["name"] for row in self.app.wizard.interfaces()}
            errors = validate_config(config, names, rerun=self.app.wizard.rerun)
            if path.endswith("/validate") and isinstance(body.get("step"), int):
                fields = {
                    1: {"role", "primary_url"},
                    2: {"interface", "mode", "address", "netmask", "gateway", "dns"},
                    3: {"hostname"},
                    4: {"password", "password_confirm", "current_password"},
                }.get(body["step"])
                if fields is not None:
                    errors = {key: value for key, value in errors.items() if key in fields}
            if errors:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "errors": errors})
                return
            if path.endswith("/validate"):
                self._json(HTTPStatus.OK, {"ok": True, "errors": {}})
                return
            job = self.app.wizard.start_apply(config, client_ip=self.client_address[0])
            self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": job})
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def serve(state: WizardState, host: str, port: int, nic_fact: Path, role_fact: Path) -> None:
    httpd = WizardHTTPServer((host, port), state, nic_fact, role_fact)
    state.shutdown_for_handoff = httpd.release_port
    # The temporary listener needs TLS before the operator has chosen the
    # management address/hostname. Keep that identity separate; the installer
    # creates the persistent console identity in ``data_dir/console-tls``.
    tls = ensure_console_cert(state.paths.data_dir / "wizard", host, hostname=None)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(tls.cert_path, tls.key_path)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print(f"imagesrv-wizard: https://{host}:{port} fingerprint={tls.fingerprint_sha256}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
