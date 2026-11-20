"""#3654 (K14): הנתיבים של ערוץ השאלה למשתמש -- הקונסולה, המכונה, ומה ש-hello אומר למכונה.

**שני מקומות עונים לאותה שאלה, ותשובה אחת סוגרת אותה בשניהם:**

- הקונסולה (מנהל): רשימה ותשובה; משתמש הפצה ומי שאינו מחובר -- סירוב; אפשרות שאינה ברשימה, שאלה שאינה קיימת ושאלה
  שנסגרה -- שגיאה בשם (400 / 404 / 409), ושום דבר לא נרשם.
- המכונה (מי שליד המסך): עונה רק על שאלה על עצמה, ורק כשה-`who` של השאלה כולל אותה; מכונה שאינה רשומה -- סירוב.
- ה-hello: שאלה פתוחה על המכונה מגיעה אליה; מה שנענה או פקע מגיע אליה עם מה לעשות (ברירת המחדל הבטוחה, לא הרסנית);
  שאלה על מכונה אחרת לא; מכונה שאינה מוכרת לא מקבלת שדה.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import hello_body

pytest.importorskip("fastapi")

from server.agent import registry   # noqa: E402
from server.brain import ask        # noqa: E402

MAC = "aa:bb:cc:00:00:01"
OTHER = "aa:bb:cc:00:00:02"
WIPE = ask.Option("wipe", "למחוק את המידע הישן", destructive=True, verb_he="מחיקת המידע",
                  consequence_he="המידע הישן יימחק ולא יהיה אפשר לשחזר אותו")
SKIP = ask.Option("skip", "לדלג ולהשאיר את הדיסק")
SPEC = ask.Spec(point="T1", title_he="נמצא מידע RAID ישן על הדיסק", options=(WIPE, SKIP), safe_default="skip", ttl_s=600)
SPEC3 = ask.Spec(point="T3", title_he="שנייה", options=(WIPE, SKIP), safe_default="skip", ttl_s=60)
ADMIN_ONLY = ask.Spec(point="T2", title_he="רק מנהל", options=(WIPE, SKIP), safe_default="skip", ttl_s=600, who=("admin",))


@pytest.fixture()
def lab(server):
    ctx = server["ctx"]
    registry.add_machine(ctx.conn, MAC, "cloner-1", "grp_CLONERS", "test")
    registry.add_machine(ctx.conn, OTHER, "cloner-2", "grp_CLONERS", "test")
    return server


def open_ask(lab, spec=SPEC, mac=MAC, **kw):
    return ask.open_ask(lab["ctx"].conn, spec, entity=("machine", mac), **kw)


def decisions(lab, rule="T1"):
    return [dict(r) for r in lab["ctx"].conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (rule,))]


def hello(lab, mac=MAC):
    body = hello_body(mac)
    response = lab["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def machine_answer(lab, ask_id, option, mac=MAC):
    return lab["anon"].post("/api/v1/agent/ask-answer", json={"mac": mac, "ask_id": ask_id, "option": option})


# --- הקונסולה -----------------------------------------------------------------------------------------------------


def test_the_console_lists_open_questions_and_what_closed_recently(lab):
    first, second = open_ask(lab), open_ask(lab, mac=OTHER)
    ask.cancel(lab["ctx"].conn, second, "המכונה כבתה")
    got = lab["admin"].get("/api/console/brain/asks")
    assert got.status_code == 200, got.text
    body = got.json()
    assert [a["id"] for a in body["open"]] == [first] and [a["id"] for a in body["recent"]] == [second]
    [item] = body["open"]
    assert (item["title_he"], item["safe_default"], item["entity"], item["who"]) == (
        SPEC.title_he, "skip", {"kind": "machine", "key": MAC}, ["admin", "machine"])
    assert [o["id"] for o in item["options"]] == ["wipe", "skip"] and item["expires_in_s"] > 0


def test_the_console_history_is_30_days_with_filters_and_a_filter_it_does_not_know_is_400(lab):
    done = open_ask(lab)
    lab["admin"].post(f"/api/console/brain/asks/{done}/answer", json={"option": "wipe"})
    gone = open_ask(lab, mac=OTHER)
    ask.cancel(lab["ctx"].conn, gone, "המכונה כבתה")
    body = lab["admin"].get("/api/console/brain/asks").json()
    assert [a["id"] for a in body["history"]["items"]] == [gone, done]
    assert (body["history"]["total"], body["history"]["days"]) == (2, 30) and body["history"]["today"]["answered"] == 1
    mine = lab["admin"].get("/api/console/brain/asks", params={"who": "admin"}).json()["history"]
    assert [a["id"] for a in mine["items"]] == [done] and mine["total"] == 2
    none = lab["admin"].get("/api/console/brain/asks", params={"who": "none", "state": "cancelled"}).json()["history"]
    assert [a["id"] for a in none["items"]] == [gone] and none["items"][0]["closed_note_he"] == "המכונה כבתה"
    for bad in ({"who": "deploy"}, {"state": "open"}, {"kind": "planet"}):
        assert lab["admin"].get("/api/console/brain/asks", params=bad).status_code == 400
    for who in (lab["deploy"], lab["anon"]):
        assert who.get("/api/console/brain/asks", params={"who": "admin"}).status_code in (401, 403)


def test_the_console_answer_is_recorded_with_the_users_name_next_to_the_brains_decision(lab):
    ask_id = open_ask(lab)
    got = lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "wipe"})
    assert got.status_code == 200, got.text
    assert (got.json()["state"], got.json()["answer"], got.json()["answered_by"], got.json()["answered_role"]) == (
        "answered", "wipe", "noc", "admin")
    assert [d["verdict"] for d in decisions(lab)] == ["ASKED", "ANSWERED"] and "noc" in decisions(lab)[1]["reason"]
    assert lab["admin"].get("/api/console/brain/asks").json()["open"] == []


@pytest.mark.parametrize("body, status", [
    ({"option": "format_everything"}, 400), ({"option": ""}, 400), ({}, 400), ({"option": 7}, 400), ("x", 400)])
def test_an_answer_that_names_no_listed_option_changes_nothing(lab, body, status):
    ask_id = open_ask(lab)
    got = lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json=body)
    assert got.status_code == status, got.text
    assert [d["verdict"] for d in decisions(lab)] == ["ASKED"] and ask.resolution(lab["ctx"].conn, ask_id).state == "open"


def test_a_late_second_answer_is_409_and_the_first_stands(lab):
    ask_id = open_ask(lab)
    assert lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "skip"}).status_code == 200
    late = lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "wipe"})
    assert late.status_code == 409 and "נענתה" in late.text
    assert ask.resolution(lab["ctx"].conn, ask_id).option == "skip"


def test_an_answer_after_the_expiry_is_409_and_the_default_stands(lab):
    ask_id = open_ask(lab, now=datetime.now(timezone.utc) - timedelta(hours=1))
    late = lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "wipe"})
    assert late.status_code == 409 and "פקעה" in late.text
    res = ask.resolution(lab["ctx"].conn, ask_id)
    assert (res.state, res.option, res.by_user) == ("expired", "skip", False)
    assert [d["verdict"] for d in decisions(lab)] == ["ASKED", "EXPIRED"]


def test_a_question_that_does_not_exist_is_404(lab):
    assert lab["admin"].post("/api/console/brain/asks/4242/answer", json={"option": "skip"}).status_code == 404


def test_a_deploy_user_and_a_stranger_cannot_read_or_answer_in_the_console(lab):
    ask_id = open_ask(lab)
    for who in (lab["deploy"], lab["anon"]):
        assert who.get("/api/console/brain/asks").status_code in (401, 403)
        assert who.post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "wipe"}).status_code in (401, 403)
    assert ask.resolution(lab["ctx"].conn, ask_id).state == "open" and [d["verdict"] for d in decisions(lab)] == ["ASKED"]


# --- המכונה -------------------------------------------------------------------------------------------------------


def test_the_person_at_the_machines_screen_answers_and_it_is_recorded_as_the_machine(lab):
    ask_id = open_ask(lab)
    got = machine_answer(lab, ask_id, "skip")
    assert got.status_code == 200, got.text
    assert (got.json()["ask"]["state"], got.json()["ask"]["answered_role"], got.json()["ask"]["answered_by"]) == (
        "answered", "machine", MAC)
    assert "המסך של המכונה" in decisions(lab)[1]["reason"]
    assert lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "wipe"}).status_code == 409


def test_a_machine_cannot_answer_about_another_machine(lab):
    theirs = open_ask(lab, mac=OTHER)
    got = machine_answer(lab, theirs, "wipe", mac=MAC)
    assert got.status_code == 403 and got.json()["code"] == "forbidden"
    assert ask.resolution(lab["ctx"].conn, theirs).state == "open"


def test_a_machine_cannot_answer_a_question_that_is_for_the_console_only(lab):
    ask_id = open_ask(lab, ADMIN_ONLY)
    assert machine_answer(lab, ask_id, "wipe").status_code == 403
    assert ask.resolution(lab["ctx"].conn, ask_id).state == "open"
    assert lab["admin"].post(f"/api/console/brain/asks/{ask_id}/answer", json={"option": "skip"}).status_code == 200


def test_a_machine_that_is_not_registered_cannot_answer(lab):
    ask_id = open_ask(lab, mac="aa:bb:cc:00:00:99")
    got = machine_answer(lab, ask_id, "wipe", mac="aa:bb:cc:00:00:99")
    assert got.status_code == 403 and got.json()["code"] == "unknown_mac"
    assert ask.resolution(lab["ctx"].conn, ask_id).state == "open"


@pytest.mark.parametrize("body", [
    {"ask_id": 1, "option": "skip"}, {"mac": "nonsense", "ask_id": 1, "option": "skip"},
    {"mac": MAC, "ask_id": "1", "option": "skip"}, {"mac": MAC, "ask_id": True, "option": "skip"},
    {"mac": MAC, "ask_id": 1, "option": None}, [1, 2]])
def test_a_malformed_machine_answer_is_400(lab, body):
    open_ask(lab)
    got = lab["anon"].post("/api/v1/agent/ask-answer", json=body)
    assert got.status_code == 400, got.text
    assert [d["verdict"] for d in decisions(lab)] == ["ASKED"]


# --- מה ש-hello אומר למכונה ----------------------------------------------------------------------------------------


def test_a_machine_is_told_its_open_question_in_the_hello_and_no_other_machine_is(lab):
    ask_id = open_ask(lab)
    [mine] = hello(lab)["asks"]
    assert (mine["id"], mine["state"], mine["safe_default"], mine["effective"]) == (ask_id, "open", "skip", None)
    assert [o["label_he"] for o in mine["options"]] == ["למחוק את המידע הישן", "לדלג ולהשאיר את הדיסק"]
    assert hello(lab, OTHER)["asks"] == []


def test_the_hello_says_what_became_of_a_question_with_the_option_to_carry_out(lab):
    answered, expired = open_ask(lab), open_ask(lab, SPEC3)
    lab["admin"].post(f"/api/console/brain/asks/{answered}/answer", json={"option": "wipe"})
    lab["ctx"].conn.execute("UPDATE brain_asks SET expires_at = ? WHERE id = ?",
                            ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds"), expired))
    lab["ctx"].conn.commit()
    got = {a["id"]: a for a in hello(lab)["asks"]}
    assert (got[answered]["state"], got[answered]["effective"], got[answered]["by_user"]) == ("answered", "wipe", True)
    assert (got[expired]["state"], got[expired]["effective"], got[expired]["by_user"]) == ("expired", "skip", False)


def test_a_question_closed_long_ago_is_not_in_the_hello_any_more(lab):
    ask_id = open_ask(lab)
    ask.answer(lab["ctx"].conn, ask_id, "skip", by="noc", role="admin")
    lab["ctx"].conn.execute("UPDATE brain_asks SET closed_at = ? WHERE id = ?", (
        (datetime.now(timezone.utc) - timedelta(seconds=ask.RECENT_CLOSED_S + 60)).isoformat(timespec="seconds"), ask_id))
    lab["ctx"].conn.commit()
    assert hello(lab)["asks"] == []


def test_a_machine_the_server_does_not_know_gets_no_asks_field(lab):
    got = lab["anon"].post("/api/v1/agent/hello", json=hello_body("aa:bb:cc:00:00:77")).json()
    assert got["known"] is False and "asks" not in got


def test_a_question_that_cannot_be_read_is_null_not_an_empty_list(lab, monkeypatch):
    open_ask(lab)

    def broken(*a, **kw):
        raise RuntimeError("the table is gone")

    monkeypatch.setattr(ask, "for_machine", broken)
    assert hello(lab)["asks"] is None            # "could not read" is not "there is none"


def test_the_machine_answer_path_is_in_the_body_cap_and_depth_guards(lab):
    got = lab["anon"].post("/api/v1/agent/ask-answer", content=b"[" * 10_000 + b"]" * 10_000,
                           headers={"Content-Type": "application/json"})
    assert got.status_code == 400 and json.loads(got.text)["code"] == "bad_json"
