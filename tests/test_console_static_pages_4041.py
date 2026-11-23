"""#4041: ה-`?v=` של הקונסולה = גרסת המוצר, והשרת ממלא אותו בהגשה.

בקבצי המקור יושב placeholder קבוע (`__IMAGESRV_V__`) ואף PR לא נוגע בו. מה שנבדק
כאן הוא ההגשה: הגרסה של `/health/live` נכנסת במקום ה-placeholder ב-`/console/`,
ב-`/console/index.html` וב-`/console/monitor.html`; גרסה שלא נקראה נופלת לערך
שמשתנה בכל עלייה ולעולם לא לערך קבוע (עיקרון 5: JS ישן מול API חדש).
"""

import re
import time

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.console.static_pages import PLACEHOLDER, create_static_pages_router  # noqa: E402
from server.paths import STATIC_DIR  # noqa: E402
from server.update import VersionError  # noqa: E402

PAGES = ("/console/", "/console/index.html", "/console/monitor.html")
INCLUDE = re.compile(r'\?v=([^"&\s>]*)')


def _client(tmp_path, images_root, clock, describe) -> TestClient:
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:7712", now_fn=clock,
                     update_hooks={"describe": describe}, repo_dir="/repo")
    return TestClient(app)


def _unread(_repo):
    raise VersionError("no-tag", "git describe --tags did not return a tag")


@pytest.mark.parametrize("path", PAGES)
def test_the_page_carries_the_product_version_and_no_placeholder(tmp_path, images_root, clock, path):
    body = _client(tmp_path, images_root, clock, lambda _repo: "v0.99.1").get(path).text
    values = INCLUDE.findall(body)
    assert values and set(values) == {"v0.99.1"}, set(values)
    assert PLACEHOLDER not in body


def test_every_include_in_the_source_is_filled_monitor_js_too(tmp_path, images_root, clock):
    client = _client(tmp_path, images_root, clock, lambda _repo: "v0.99.1")
    for name in ("index.html", "monitor.html"):
        source = (STATIC_DIR / name).read_text(encoding="utf-8")
        served = client.get(f"/console/{name}").text
        assert len(INCLUDE.findall(served)) == len(INCLUDE.findall(source)) >= 5, name
    assert 'src="monitor.js?v=v0.99.1"' in client.get("/console/monitor.html").text


def test_a_new_version_means_new_urls_in_the_browser(tmp_path, images_root, clock):
    old = _client(tmp_path / "a", images_root, clock, lambda _repo: "v0.99.1").get("/console/").text
    new = _client(tmp_path / "b", images_root, clock, lambda _repo: "v0.99.2").get("/console/").text
    assert 'src="console.js?v=v0.99.1"' in old and 'src="console.js?v=v0.99.2"' in new
    assert old != new


@pytest.mark.parametrize("path", PAGES)
def test_an_unread_version_falls_back_to_a_boot_value_not_a_fixed_one(tmp_path, images_root, clock, path):
    body = _client(tmp_path, images_root, clock, _unread).get(path).text
    values = set(INCLUDE.findall(body))
    assert len(values) == 1, values
    (value,) = values
    assert value.startswith("boot") and PLACEHOLDER not in body, value


def test_the_boot_value_is_stable_within_a_boot_and_changes_between_boots(tmp_path, images_root, clock):
    first = _client(tmp_path / "a", images_root, clock, _unread)
    seen = {INCLUDE.findall(first.get("/console/").text)[0] for _ in range(3)}
    assert len(seen) == 1, "the value moved inside one boot: the browser would never cache"
    time.sleep(0.02)
    second = _client(tmp_path / "b", images_root, clock, _unread)
    assert INCLUDE.findall(second.get("/console/").text)[0] not in seen


def test_a_version_that_raises_does_not_take_the_console_down():
    def boom():
        raise RuntimeError("the version hook exploded")
    app = FastAPI()
    app.include_router(create_static_pages_router(boom))
    r = TestClient(app).get("/console/")
    assert r.status_code == 200 and INCLUDE.findall(r.text)[0].startswith("boot")


def test_a_version_is_escaped_so_it_cannot_break_out_of_the_attribute():
    app = FastAPI()
    app.include_router(create_static_pages_router(lambda: 'v1"><script>alert(1)</script>'))
    body = TestClient(app).get("/console/").text
    assert "<script>alert(1)" not in body and PLACEHOLDER not in body


def test_the_page_still_revalidates_and_answers_304_on_a_matching_etag(tmp_path, images_root, clock):
    client = _client(tmp_path, images_root, clock, lambda _repo: "v0.99.1")
    first = client.get("/console/")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-cache"
    again = client.get("/console/", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    other = _client(tmp_path / "b", images_root, clock, lambda _repo: "v0.99.2")
    assert other.get("/console/", headers={"If-None-Match": first.headers["etag"]}).status_code == 200


def test_the_other_static_files_are_still_served_from_the_mount(tmp_path, images_root, clock):
    r = _client(tmp_path, images_root, clock, lambda _repo: "v0.99.1").get("/console/console.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
