"""‏#1302 שלב ב' (נדב 12/10): לוגו ImageSrv בקונסולה, ובלי אפשרות להחליף לוגו.

"שלא תהיה אפשרות כזאת" — הסרה מהקוד, לא הסתרה ב-UI: הנתיבים של לוגו המוסד
(‏`/api/console/branding/logo` ו-`/api/v1/agent/branding/logo`) אינם קיימים,
‏`server/branding.py` אינו קיים, ובקונסולה אין כרטיס העלאה. הסמל הקבוע מוגש
מ-server/static, והכותרת, מסך הכניסה וה-favicon מצביעים עליו.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_the_console_logo_route_is_gone(server, method):
    pytest.importorskip("fastapi")
    kwargs = {"content": PNG, "headers": {"Content-Type": "image/png"}} if method == "post" else {}
    for client in ("anon", "admin"):
        r = getattr(server[client], method)("/api/console/branding/logo", **kwargs)
        assert r.status_code in (404, 405), (client, method, r.status_code)


def test_the_agent_port_logo_route_is_gone(server):
    pytest.importorskip("fastapi")
    assert server["anon"].get("/api/v1/agent/branding/logo").status_code == 404


def test_no_branding_module_and_no_router_wiring():
    assert not (REPO / "server" / "branding.py").exists()
    for name in ("app.py", "agent/api.py"):
        text = (REPO / "server" / name).read_text(encoding="utf-8")
        assert "branding" not in text, name


def test_the_console_has_no_logo_upload_ui():
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    for gone in ("branding/logo", "logo-input", "settingsLogoUpload", "settingsLogoClear",
                 "brandingCardHtml", "LOGO_EXISTS", "loadLogo", "לוגו המוסד"):
        assert gone not in js, gone
    assert not re.search(r'title: "מיתוג"', js)


def test_the_top_bar_and_login_show_the_imagesrv_lockup():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    top = page[page.index('<header class="topbar"'):]
    top = top[:top.index("</header>")]
    assert re.search(r'<img[^>]+src="logo-lockup-compact-dark\.svg\?v=[\d.]+"[^>]+alt="ImageSrv"', top)
    assert "brandmark" not in top and 'class="mark"' not in top
    login = page[page.index('<div id="login"'):]
    login = login[:login.index('id="login-body"')]
    assert re.search(r'<img[^>]+class="lockup lockup-light"[^>]+src="logo-lockup\.svg\?v=[\d.]+"', login)
    assert re.search(r'<img[^>]+class="lockup lockup-dark"[^>]+src="logo-lockup-dark\.svg\?v=[\d.]+"', login)
    # ‏SVG עם viewBox בלבד קורס ל-0 תחת max-height (CLAUDE.md) — גובה מפורש.
    for tag in re.findall(r"<img[^>]+logo-lockup[^>]*>", page):
        assert re.search(r'\sheight="\d+"', tag), tag
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"] #login .lockup-light{display:none}' in css
    assert ':root:not([data-theme="dark"]) #login .lockup-dark{display:none}' in css


@pytest.mark.parametrize("page", ["index.html", "monitor.html"])
def test_favicons_and_title_say_imagesrv(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert re.search(r"<title>ImageSrv — ", html)
    assert "ImageCtl" not in html
    for href in ("favicon.svg", "favicon.ico", "favicon-16.png", "favicon-32.png", "apple-touch-icon.png"):
        assert re.search(rf'<link rel="(?:icon|apple-touch-icon)"[^>]+href="{re.escape(href)}\?v=[\d.]+"', html), href
        assert (STATIC / href).is_file(), href


def test_the_static_symbol_is_the_k_mark():
    """‏logo.svg הוא הסמל K (12 ריבועים בגריד 64) — לא הלוגו הווקטורי הישן."""
    svg = (STATIC / "logo.svg").read_text(encoding="utf-8")
    assert "<title>ImageSrv</title>" in svg and "ImageCtl" not in svg
    assert len(re.findall(r"<rect ", svg)) == 12
    for name in ("logo-lockup.svg", "logo-lockup-dark.svg", "logo-lockup-compact-dark.svg"):
        assert (STATIC / name).read_text(encoding="utf-8") == \
            (REPO / "docs" / "brand" / name).read_text(encoding="utf-8"), name


def test_the_wizard_pages_carry_the_favicon(tmp_path):
    page = (REPO / "server" / "wizard" / "static" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in page
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in page
    import http.client  # noqa: PLC0415
    import threading  # noqa: PLC0415
    from server.wizard.core import WizardState  # noqa: PLC0415
    from server.wizard.httpd import WizardHTTPServer  # noqa: PLC0415
    from test_wizard import NICS, _paths  # noqa: PLC0415
    state = WizardState(_paths(tmp_path, tmp_path / "u1", tmp_path / "u2"), dev=True,
                        interfaces_provider=lambda: NICS)
    nic, role = tmp_path / "nic", tmp_path / "role"
    nic.write_text("", encoding="utf-8")
    role.write_text("", encoding="utf-8")
    wiz = WizardHTTPServer(("127.0.0.1", 0), state, nic, role)
    serving = threading.Thread(target=wiz.serve_forever, daemon=True)
    serving.start()
    try:
        for name, kind in (("favicon.svg", "image/svg+xml"), ("favicon.ico", "image/")):
            conn = http.client.HTTPConnection("127.0.0.1", wiz.server_port, timeout=2)
            try:
                conn.request("GET", "/" + name)
                r = conn.getresponse()
                assert r.status == 200, name
                assert r.getheader("Content-Type", "").startswith(kind), r.getheader("Content-Type")
                assert r.read() == (STATIC / name).read_bytes(), name
            finally:
                conn.close()
    finally:
        wiz.shutdown(); wiz.server_close(); serving.join(2)


def test_the_visible_name_outside_the_console_is_imagesrv():
    """שאריות שלב א' (#1302): הכותרת של מסכי הסוכן, שם שולחן המוניטור ב-VNC,
    והודעות האתחול הראשון על tty1. שמות פנימיים (imagesrv-*, UINPUT_NAME) נשארים."""
    ui = (REPO / "agent" / "lib" / "gui" / "ui.sh").read_text(encoding="utf-8")
    assert 'echo "  ImageSrv"' in ui and '"  ImageCtl"' not in ui
    mon = (REPO / "agent" / "monitor.c").read_text(encoding="utf-8")
    assert 'screen->desktopName = "ImageSrv monitor";' in mon
    first = (REPO / "tools" / "iso" / "firstboot.sh").read_text(encoding="utf-8")
    assert "ImageCtl:" not in first and "ImageSrv: first boot failed" in first
