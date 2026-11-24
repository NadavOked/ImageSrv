#!/usr/bin/env python3
"""מייצר את grub.cfg הקבוע שיושב על ה-TFTP.

    python tools/render_bootstrap.py http://10.44.12.10:7711 > grub.cfg

הכתובת היא של מאזין ה-/boot של GRUB (7711, HTTP), לא של ערוץ הסוכן (7712, HTTPS).

מריצים את זה פעם אחת בהתקנה, ושוב רק אם כתובת השרת משתנה. הקובץ שנוצר
מועתק לשורש ה-TFTP תחת grub/grub.cfg, ליד ה-shim וה-GRUB החתומים.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boot.grub_menu import GrubConfig, render_bootstrap  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        config = GrubConfig(server_base=argv[1])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render_bootstrap(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
