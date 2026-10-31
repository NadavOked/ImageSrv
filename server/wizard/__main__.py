"""``python -m server.wizard`` entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import WizardPaths, WizardState, list_interfaces
from .httpd import serve


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ImageSrv first-boot HTTPS wizard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("--data-dir", type=Path, default=Path("/var/lib/imagesrv"))
    p.add_argument("--status-file", type=Path, default=None)
    p.add_argument("--stamp-file", type=Path, default=None)
    p.add_argument("--installer-nic", type=Path, default=Path("/etc/imagesrv/installer-nic"))
    p.add_argument("--installer-role", type=Path, default=Path("/etc/imagesrv/installer-role"))
    p.add_argument("--source-dir", type=Path, default=Path("/opt/imagesrv-src"))
    p.add_argument("--app-dir", type=Path, default=Path("/opt/imagesrv"))
    p.add_argument("--installer", type=Path, default=None)
    p.add_argument("--verify", type=Path, default=None)
    p.add_argument("--http-root", type=Path, default=Path("/srv/imagesrv/boot"))
    p.add_argument("--rerun", action="store_true")
    p.add_argument("--dev", action="store_true", help="use a fake installer and do not change the host")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    stamp = args.stamp_file or args.data_dir / ".firstboot-done"
    if stamp.exists() and not args.rerun:
        print(f"imagesrv-wizard: ImageSrv כבר הותקן ({stamp}); להרצה חוזרת נדרש --rerun", file=sys.stderr)
        return 2
    args.data_dir.mkdir(parents=True, exist_ok=True)
    status_file = args.status_file or (args.data_dir / "firstboot.status" if args.dev else Path("/etc/imagesrv/firstboot.status"))
    paths = WizardPaths(
        source_dir=args.source_dir, app_dir=args.app_dir, data_dir=args.data_dir,
        status_file=status_file, stamp_file=stamp,
        installer=args.installer or args.source_dir / "deploy" / "setup-boot-server.sh",
        verify=args.verify or args.app_dir / "deploy" / "verify-boot-payload.sh",
        http_root=args.http_root,
    )
    provider = list_interfaces
    if args.dev:
        provider = lambda: list_interfaces() or [{
            "name": "management0", "mac": "02:00:00:00:09:10", "link": "up",
            "addresses": ["192.0.2.10/24"], "current_ip": "192.0.2.10/24",
        }]
    state = WizardState(paths=paths, rerun=args.rerun, dev=args.dev,
                        interfaces_provider=provider)
    serve(state, args.host, args.port, args.installer_nic, args.installer_role)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
