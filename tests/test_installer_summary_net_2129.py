"""סיכום המתקין: השער וה-DNS, והכרטיס לפי שם קריא + MAC (#2129).

ב-rc3 (24/10) שורת הרשת בסיכום הייתה `eth0 · 192.168.70.77 / 255.255.252.0`:
`eth0` הוא שם הכרטיס **במתקין** ולא במערכת שתותקן (`ens33`), והשער וה-DNS
לא הוצגו, כך שאי אפשר היה לאשר אותם לפני "החל התקנה". המוקאפ
(`install-5-summary`) מציג "· שער … · DNS …" באותה שורה.

הטסטים מריצים את `summary()` האמיתי של `screens_install_final.c` דרך דרייבר C
(`text_draw_r` עטוף בזמן הקישור — כל מחרוזת שהמסך מצייר מדווחת עם ה-y
והגובה שלה), ובודקים את מה שהמפעיל רואה:

* סטטי: שם קריא (הדגם, או "כרטיס רשת N" כמו במסך הרשת, #2096) + MAC, הכתובת,
  השער וה-DNS — ובלי `eth0`;
* DHCP: מה שיש לכרטיס עכשיו — כתובת, שער ו-DNS מה-DHCP;
* שער / DNS ריקים (הם "רשות") נאמרים במפורש, לא נעלמים;
* שורת הרשת גדלה כשהיא נשברת, והשורה הבאה ("שם השרת") מתחילה אחריה.

בונים את הגואי (C, pango/cairo) — לינוקס בלבד.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui"
MAC = "00:15:5d:44:3c:09"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("bash", shutil.which("bash")),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("make", shutil.which("make")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="native installer is built and rendered on Linux",
)

# argv: summary <static 0|1> <model> <gateway> <dns> -- the static values are
# what the operator typed; the NIC itself has the DHCP ones (10.68.0.x).
DRIVER = r'''
#include "%(final_c)s"
#include <stdlib.h>
void __real_text_draw_r(cairo_t *cr, Text *t, double x_right, double y, Rgb c);
static int log_draws;
void __wrap_text_draw_r(cairo_t *cr, Text *t, double x_right, double y, Rgb c) {
    if (log_draws) printf("draw\t%%.1f\t%%d\t%%s\n", y, t->h, pango_layout_get_text(t->layout));
    __real_text_draw_r(cr, t, x_right, y, c);
}
static App app;
int main(int argc, char **argv) {
    if (argc != 6 || strcmp(argv[1], "summary")) return 2;
    InstallState *s = &app.install; app.theme = &THEME_LIGHT;
    s->nnics = 1; s->nic = 0; s->static_mode = atoi(argv[2]);
    InstallNic *n = &s->nics[0];
    snprintf(n->name, sizeof n->name, "eth0"); snprintf(n->model, sizeof n->model, "%%s", argv[3]);
    snprintf(n->mac, sizeof n->mac, "%(mac)s"); snprintf(n->current, sizeof n->current, "10.68.0.61/22");
    snprintf(n->address, sizeof n->address, "10.68.0.61"); snprintf(n->netmask, sizeof n->netmask, "255.255.252.0");
    snprintf(n->gateway, sizeof n->gateway, "10.68.0.1"); snprintf(n->dns, sizeof n->dns, "10.68.0.2");
    snprintf(s->address, sizeof s->address, "192.168.70.77"); snprintf(s->netmask, sizeof s->netmask, "255.255.252.0");
    snprintf(s->gateway, sizeof s->gateway, "%%s", argv[4]); snprintf(s->dns, sizeof s->dns, "%%s", argv[5]);
    snprintf(s->hostname, sizeof s->hostname, "imagesrv-ta"); snprintf(s->timezone, sizeof s->timezone, "Asia/Jerusalem");
    snprintf(s->admin_user, sizeof s->admin_user, "admin");
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1280, 800);
    cairo_t *cr = cairo_create(sf);
    InstallLayout l = { { 0, 0, 1030, 800 }, { 135, 105, 760, 615 }, { 0, 736, 1030, 64 }, { 1030, 0, 250, 800 }, 895 };
    log_draws = 1; summary(&app, cr, l); log_draws = 0;
    cairo_destroy(cr); cairo_surface_destroy(sf); return 0;
}
'''


def _driver(tmp_path: Path) -> Path:
    source = tmp_path / "summary_driver.c"
    source.write_text(DRIVER % {"final_c": (GUI / "src" / "screens_install_final.c").as_posix(), "mac": MAC},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "summary-driver"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\\n" | grep -v -x -e src/main.c -e src/screens_install_final.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -Wl,--wrap=text_draw_r -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(source)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


def _summary(binary: Path, static: str, model: str, gateway: str, dns: str) -> list[tuple[float, int, str]]:
    """Every string the summary drew: (y, height, text)."""
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "summary", static, model, gateway, dns], capture_output=True,
                         text=True, encoding="utf-8", timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, (run.returncode, run.stderr)
    draws = []
    for line in run.stdout.splitlines():
        if line.startswith("draw\t"):
            _, y, h, text = line.split("\t", 3)
            draws.append((float(y), int(h), text))
    assert any(text == "שם השרת" for _, _, text in draws), f"the summary drew no rows -- the wrap is not in place: {draws}"
    return draws


def _with(draws, *parts: str) -> list[tuple[float, int, str]]:
    return [d for d in draws if all(p in d[2] for p in parts)]


@NATIVE
def test_static_summary_shows_the_readable_nic_its_mac_the_gateway_and_the_dns(tmp_path) -> None:
    binary = _driver(tmp_path)
    draws = _summary(binary, "1", "Intel 82574L", "192.168.68.1", "192.168.68.2")
    texts = [text for _, _, text in draws]
    assert not [t for t in texts if "eth0" in t], f"the installer's NIC name is on the summary: {texts}"
    assert _with(draws, "Intel 82574L", MAC), f"no readable name + MAC: {texts}"
    assert _with(draws, "192.168.70.77", "255.255.252.0"), texts
    assert _with(draws, "שער", "192.168.68.1"), f"no gateway: {texts}"
    assert _with(draws, "DNS", "192.168.68.2"), f"no DNS: {texts}"
    # the DHCP values the NIC has now are not what a static install applies
    assert not _with(draws, "10.68.0.1"), texts


@NATIVE
def test_dhcp_summary_shows_what_the_nic_has_now(tmp_path) -> None:
    binary = _driver(tmp_path)
    draws = _summary(binary, "0", "Intel 82574L", "", "")
    texts = [text for _, _, text in draws]
    assert _with(draws, "DHCP", "10.68.0.61/22"), texts
    assert _with(draws, "שער", "10.68.0.1"), f"no gateway: {texts}"
    assert _with(draws, "DNS", "10.68.0.2"), f"no DNS: {texts}"


@NATIVE
def test_an_empty_gateway_or_dns_is_said_and_a_nic_without_a_model_gets_its_number(tmp_path) -> None:
    binary = _driver(tmp_path)
    draws = _summary(binary, "1", "", "", "")
    texts = [text for _, _, text in draws]
    assert _with(draws, "ללא שער", "ללא DNS"), f"an optional field left empty vanished: {texts}"
    assert _with(draws, "כרטיס רשת 1", MAC), f"not the network screen's name (#2096): {texts}"
    assert not [t for t in texts if "eth0" in t], texts


@NATIVE
def test_the_network_row_grows_and_the_next_row_starts_below_it(tmp_path) -> None:
    binary = _driver(tmp_path)
    dns = ", ".join(f"10.10.10.{i}" for i in range(10, 17))   # 89 bytes: s->dns is 96
    draws = _summary(binary, "1", "Intel 82574L Gigabit Network Connection", "192.168.68.1", dns)
    net = _with(draws, "DNS", "10.10.10.16")
    assert net, [text for _, _, text in draws]
    y, h, _ = net[0]
    single = _with(draws, "imagesrv-ta")[0][1]   # one line of the same 13px value (#2764: the name's row no longer carries the zone)
    assert h > 1.5 * single, "the DNS line did not wrap -- the test proves nothing"
    name_y = _with(draws, "שם השרת")[0][0]
    assert name_y >= y + h, f"'שם השרת' at y={name_y} is drawn over the network row ({y}..{y + h})"
