"""Entry point for ``python3 -m server.dcui``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .app import render_demo, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ImageSrv physical server console")
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/imagesrv"))
    parser.add_argument("--render-demo", action="store_true",
                        help="print the five approved 80x25 screens")
    parser.add_argument("--gui-frames", type=Path, metavar="DIR",
                        help="write the graphical screen's sample frames (#1203) and exit")
    parser.add_argument("--text", action="store_true",
                        help="the 80x25 text screen even when the graphical one could run")
    args = parser.parse_args(argv)
    if args.render_demo:
        print(render_demo())
        return 0
    if args.gui_frames:
        from . import samples
        for path in samples.write(args.gui_frames):
            print(path)
        return 0
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        print("imagesrv-dcui: root privileges are required", file=sys.stderr)
        return 3
    run(args.data_dir, text_only=args.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
