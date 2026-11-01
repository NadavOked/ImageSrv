"""#1751 (Nadav 18/10): the server mails its operator, like QNAP's Notification
Center -- sending accounts, rules, history.

Every test talks to a fake SMTP server on 127.0.0.1 and a random port
(``FakeSMTP``, socketserver) and, for OAuth, a fake identity provider
(``FakeIdP``). No real mail and no real HTTP leave the machine.

What is pinned here:
* success is **only** 250 on DATA -- a 554/451/535, a refused recipient list,
  a server that does not offer STARTTLS, a server that never answers (hard
  deadline), or an account with auth and no secret are failures (principle 5);
* accounts: a provider fills host/port/encryption/auth (Direct Send derives
  the MX); retargeting without a password deletes the stored secrets;
* rules: categories x minimum severity, one mail per recipient language
  (he right-to-left, en left-to-right), the default rule creates itself with
  the first recipient;
* the queue: coalescing per (rule, kind, language), gather delay, rate window,
  backoff, visible give-up, a blocked account spends no attempts;
* OAuth device code: the code is shown, the device code and the tokens never
  leave the server, the refresh token lands in a 0600 file, XOAUTH2 on send;
* the event sources: journal tail, heartbeat downtime, update check, peers;
* the API: every route admin only, ``deploy`` 403.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import socketserver
import threading
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header

import pytest

from server.system import email_alerts as ea
from server.system import email_config as ec
from server.system import email_oauth as eo
from server.db import connect, get_setting, journal, set_setting

T0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)


# --- a fake SMTP server -------------------------------------------------------

class FakeSMTP:
    """A minimal SMTP server: what it answers is set per test, what it got is
    recorded. ``silent`` accepts the connection and never greets."""

    def __init__(self):
        self.data_code = 250
        self.rcpt_refuse: set[str] = set()
        self.auth_code = 235
        self.offer_starttls = False
        self.offer_auth = True
        self.silent = False
        self.messages: list[dict] = []
        self.commands: list[str] = []
        self.auth: list[str] = []
        self._release = threading.Event()
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, text: str) -> None:
                self.wfile.write((text + "\r\n").encode())
                self.wfile.flush()

            def handle(self):
                if owner.silent:
                    owner._release.wait(5)
                    return
                self.reply("220 fake ESMTP")
                mail_from, rcpts = None, []
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    verb = line.split(" ", 1)[0].upper()
                    owner.commands.append(verb)
                    if verb in ("EHLO", "HELO"):
                        caps = ["250-fake"]
                        if owner.offer_starttls:
                            caps.append("250-STARTTLS")
                        if owner.offer_auth:
                            caps.append("250-AUTH PLAIN XOAUTH2")
                        caps.append("250 8BITMIME")
                        for cap in caps:
                            self.wfile.write((cap + "\r\n").encode())
                        self.wfile.flush()
                    elif verb == "AUTH":
                        owner.auth.append(base64.b64decode(line.split()[-1]).decode())
                        self.reply(f"{owner.auth_code} auth")
                    elif verb == "MAIL":
                        mail_from, rcpts = line, []
                        self.reply("250 ok")
                    elif verb == "RCPT":
                        addr = line.split(":", 1)[1].strip().strip("<>")
                        if addr in owner.rcpt_refuse:
                            self.reply("550 5.1.1 no such user")
                        else:
                            rcpts.append(addr)
                            self.reply("250 ok")
                    elif verb == "DATA":
                        self.reply("354 go")
                        body = b""
                        while True:
                            chunk = self.rfile.readline()
                            if chunk in (b".\r\n", b""):
                                break
                            body += chunk
                        if owner.data_code == 250:
                            owner.messages.append({"from": mail_from, "rcpts": rcpts,
                                                   "raw": body})
                        self.reply(f"{owner.data_code} data")
                    elif verb == "QUIT":
                        self.reply("221 bye")
                        return
                    elif verb == "STARTTLS":
                        self.reply("454 no tls here")
                    else:
                        self.reply("250 ok")

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def close(self):
        self._release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@pytest.fixture()
def smtp():
    fake = FakeSMTP()
    yield fake
    fake.close()


@pytest.fixture()
def conn(tmp_path):
    (tmp_path / "data").mkdir()
    return connect(tmp_path / "data" / "imagesrv.db")


def acc_for(fake: FakeSMTP, **over) -> dict:
    acc = {"id": "acc_test", "name": "בדיקה", "provider": "custom", "host": "127.0.0.1",
           "port": fake.port, "security": "none", "auth": "none", "username": "",
           "from_addr": "imagesrv@college.test", "oauth_client_id": "", "oauth_tenant": "organizations"}
    acc.update(over)
    return acc


def configure(conn, fake: FakeSMTP, *, rules=None, **acc_over) -> dict:
    """One relay-style account and one rule ("everything", he + en recipients) unless told otherwise."""
    acc = acc_for(fake, **acc_over)
    if rules is None:
        rules = [{"id": "rule_all", "name": "הכול", "enabled": True, "account_id": acc["id"],
                  "categories": list(ec.CATEGORIES), "min_severity": "info",
                  "recipients": [{"address": "nadav@college.test", "lang": "he"},
                                 {"address": "it@college.test", "lang": "he"}]}]
    doc = {"accounts": [acc], "rules": rules}
    ec.save(conn, doc)
    return doc


def item(text):
    return lambda lang: text


def subject_of(raw: bytes) -> str:
    return str(make_header(decode_header(message_from_bytes(raw)["Subject"])))


def body_of(raw: bytes) -> str:
    msg = message_from_bytes(raw)
    part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    return part.get_payload(decode=True).decode("utf-8")


def parts(raw: bytes) -> dict:
    msg = message_from_bytes(raw)
    return {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
            for p in msg.walk() if not p.is_multipart()}


def queue(conn) -> list:
    return conn.execute("SELECT * FROM email_queue ORDER BY id").fetchall()


def events(conn, name: str) -> list[str]:
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (name,))]


def msg_for(conn, acc, rcpts, kind="round_failed", items=("שורה א",), lang="he"):
    return ea.build_message(conn, lang, acc["from_addr"], list(rcpts), kind, list(items))


RCPTS = ["nadav@college.test", "it@college.test"]


# --- send: success only on 250 -------------------------------------------------

def test_a_mail_is_delivered_only_when_data_gets_250(conn, smtp):
    acc = acc_for(smtp)
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS))
    assert (result.ok, result.code) == (True, 250)
    got = smtp.messages[0]
    assert got["rcpts"] == RCPTS
    assert "[ImageSrv]" in subject_of(got["raw"]) and "סבב שיכפול נכשל" in subject_of(got["raw"])
    assert "שורה א" in body_of(got["raw"])


@pytest.mark.parametrize("code", [554, 451])
def test_a_data_answer_other_than_250_is_a_failure(conn, smtp, code):
    smtp.data_code = code
    acc = acc_for(smtp)
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS))
    assert (result.ok, result.code) == (False, code)


def test_all_recipients_refused_is_a_failure_and_data_is_never_sent(conn, smtp):
    smtp.rcpt_refuse = set(RCPTS)
    acc = acc_for(smtp)
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS))
    assert result.ok is False and result.code == 550
    assert "DATA" not in smtp.commands


def test_one_refused_recipient_is_reported_while_the_rest_get_the_mail(conn, smtp):
    smtp.rcpt_refuse = {"it@college.test"}
    acc = acc_for(smtp)
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS))
    assert result.ok is True and list(result.refused) == ["it@college.test"]
    assert smtp.messages[0]["rcpts"] == ["nadav@college.test"]


def test_a_rejected_login_is_a_failure_with_its_code(conn, smtp):
    smtp.auth_code = 535
    acc = acc_for(smtp, auth="password", username="svc")
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS), password="secret-pw")
    assert (result.ok, result.code) == (False, 535) and smtp.messages == []


def test_oauth_sends_xoauth2_with_the_user_and_the_token(conn, smtp):
    acc = acc_for(smtp, auth="oauth", username="alerts@college.test")
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS), oauth_token="tok-123")
    assert result.ok is True
    assert smtp.auth == ["user=alerts@college.test\x01auth=Bearer tok-123\x01\x01"]


def test_an_account_with_auth_and_no_secret_never_connects(conn, smtp):
    for auth in ("password", "oauth"):
        acc = acc_for(smtp, auth=auth, username="svc")
        result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS))
        assert result.ok is False
    assert smtp.commands == []


def test_starttls_that_is_not_offered_is_a_failure_not_a_plaintext_send(conn, smtp):
    acc = acc_for(smtp, security="starttls")
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS))
    assert result.ok is False and "STARTTLS" in result.message
    assert "MAIL" not in smtp.commands and smtp.messages == []


def test_a_server_that_never_answers_fails_within_the_hard_deadline(conn, smtp):
    smtp.silent = True
    acc = acc_for(smtp)
    started = time.monotonic()
    result = ea.send(acc, RCPTS, msg_for(conn, acc, RCPTS), timeout=0.5)
    assert time.monotonic() - started < 3
    assert result.ok is False and result.code is None


def test_a_closed_port_is_a_failure_without_an_smtp_code(conn):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    acc = {"id": "a", "host": "127.0.0.1", "port": port, "security": "none", "auth": "none",
           "username": "", "from_addr": "a@college.test"}
    result = ea.send(acc, ["b@college.test"], msg_for(conn, acc, ["b@college.test"]), timeout=3)
    assert result.ok is False and result.code is None


# --- accounts: providers fill everything ---------------------------------------

def test_office365_fills_host_port_starttls_and_oauth():
    acc, *_ = ec.merge_account(None, {"provider": "office365"}, False)
    assert (acc["host"], acc["port"], acc["security"], acc["auth"]) == (
        "smtp.office365.com", 587, "starttls", "oauth")


def test_direct_send_derives_the_mx_host_from_the_sender_domain_and_stays_editable():
    acc, *_ = ec.merge_account(None, {"provider": "o365_direct",
                                      "from_addr": "imagesrv@college.ac.il"}, False)
    assert (acc["host"], acc["port"], acc["auth"]) == (
        "college-ac-il.mail.protection.outlook.com", 25, "none")
    edited, *_ = ec.merge_account(acc, {"host": "mx.college.ac.il"}, False)
    assert edited["host"] == "mx.college.ac.il"


def test_relay_and_gmail_presets():
    relay, *_ = ec.merge_account(None, {"provider": "relay", "host": "mail.college.local"}, False)
    assert (relay["port"], relay["security"], relay["auth"], relay["host"]) == (
        25, "none", "none", "mail.college.local")
    gmail, *_ = ec.merge_account(None, {"provider": "gmail"}, False)
    assert (gmail["host"], gmail["port"], gmail["security"]) == ("smtp.gmail.com", 587, "starttls")


@pytest.mark.parametrize("body, needle", [
    ({"provider": "o365_direct", "username": "svc"}, "בלי שם משתמש"),
    ({"provider": "relay", "auth": "password"}, "אינו תומך"),
    ({"provider": "custom", "auth": "password", "security": "none"}, "בלי הצפנה"),
    ({"from_addr": "a@b.test\r\nBcc: evil@x.test"}, "תווי בקרה"),
    ({"port": 0}, "פורט"),
    ({"provider": "nope"}, "לא מוכר"),
])
def test_bad_accounts_are_refused(body, needle):
    with pytest.raises(ec.ConfigError, match=needle):
        ec.merge_account(None, body, False)


@pytest.mark.parametrize("change", [
    {"provider": "gmail"}, {"host": "evil.test"}, {"port": 2525}, {"security": "tls"},
    {"username": "other"}, {"oauth_client_id": "other-app"}])
def test_retargeting_an_oauth_account_always_clears_its_token(change):
    """Security review: a password must not "launder" a retarget of an OAuth account --
    the old refresh token would keep working against the new target."""
    current, *_ = ec.merge_account(None, {"provider": "office365", "username": "svc@college.test",
                                          "oauth_client_id": "app-1"}, False)
    assert current["auth"] == "oauth"
    _, password, _, clear = ec.merge_account(current, change, True)
    assert password is None and clear is True
    if "provider" not in change:                      # gmail is oauth too; a password is refused anyway
        with pytest.raises(ec.ConfigError, match="סיסמה"):
            ec.merge_account(current, {**change, "password": "new"}, True)
    _, _, _, clear_name = ec.merge_account(current, {"name": "חדש"}, True)
    assert clear_name is False                         # not a retarget


def test_retargeting_a_password_account_clears_even_with_a_new_password():
    current, *_ = ec.merge_account(None, {"provider": "custom", "auth": "password", "username": "svc",
                                          "host": "mail.college.test"}, False)
    _, password, _, clear = ec.merge_account(current, {"host": "evil.test", "password": "new"}, True)
    assert (password, clear) == ("new", True)          # cleared, then the new one is written


@pytest.mark.parametrize("body, needle", [
    ({"provider": "relay", "password": "x"}, "סיסמה"),
    ({"provider": "office365", "password": "x"}, "סיסמה"),
    ({"provider": "custom", "auth": "password", "username": "u", "oauth_client_secret": "s"}, "client secret"),
])
def test_a_secret_that_does_not_match_the_auth_is_refused(body, needle):
    with pytest.raises(ec.ConfigError, match=needle):
        ec.merge_account(None, body, False)


@pytest.mark.parametrize("tenant", ["evil.test/x", "a@b.test", "..", "x?y", "evil.test#", "a b",
                                    "login.microsoftonline.com.evil.test:443", "%2e%2e"])
def test_a_tenant_that_is_not_a_guid_domain_or_alias_is_refused(tenant):
    with pytest.raises(ec.ConfigError, match="tenant"):
        ec.merge_account(None, {"provider": "office365", "oauth_tenant": tenant}, False)


@pytest.mark.parametrize("tenant", ["organizations", "common", "college.ac.il",
                                    "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"])
def test_valid_tenants_are_kept(tenant):
    acc, *_ = ec.merge_account(None, {"provider": "office365", "oauth_tenant": tenant}, False)
    assert acc["oauth_tenant"] == tenant


# --- rules: categories, severity, per-recipient language -----------------------

def test_a_rule_receives_only_its_categories_from_its_severity(conn, smtp):
    configure(conn, smtp, rules=[{"id": "r1", "name": "", "enabled": True, "account_id": "acc_test",
                                  "categories": ["cloning", "system"], "min_severity": "warn",
                                  "recipients": [{"address": "nadav@college.test", "lang": "he"}]}])
    assert ea.enqueue(conn, "round_failed", item("x"), now=T0) == 1     # cloning, err
    assert ea.enqueue(conn, "server_started", item("x"), now=T0) == 1   # system, warn
    assert ea.enqueue(conn, "auto_reboot", item("x"), now=T0) == 0      # system, info
    assert ea.enqueue(conn, "disk_red", item("x"), now=T0) == 0         # disks: not in the rule
    assert {r["kind"] for r in queue(conn)} == {"round_failed", "server_started"}


def test_a_disabled_rule_queues_nothing(conn, smtp):
    doc = configure(conn, smtp)
    doc["rules"][0]["enabled"] = False
    ec.save(conn, doc)
    assert ea.enqueue(conn, "round_failed", item("x"), now=T0) == 0


def test_each_recipient_gets_the_mail_in_their_own_language(conn, smtp):
    configure(conn, smtp, rules=[{"id": "r1", "name": "", "enabled": True, "account_id": "acc_test",
                                  "categories": list(ec.CATEGORIES), "min_severity": "info",
                                  "recipients": [{"address": "nadav@college.test", "lang": "he"},
                                                 {"address": "vendor@partner.test", "lang": "en"}]}])
    ea.enqueue(conn, "round_failed", lambda lang: "שורה" if lang == "he" else "line", now=T0)
    ea.deliver_due(conn, None, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    by_rcpt = {m["rcpts"][0]: m["raw"] for m in smtp.messages}
    assert set(by_rcpt) == {"nadav@college.test", "vendor@partner.test"}
    he, en = parts(by_rcpt["nadav@college.test"]), parts(by_rcpt["vendor@partner.test"])
    assert 'dir="rtl"' in he["text/html"] and 'lang="he"' in he["text/html"] and "text-align:right" in he["text/html"]
    assert 'dir="ltr"' in en["text/html"] and 'lang="en"' in en["text/html"] and "text-align:left" in en["text/html"]
    assert "שורה" in he["text/plain"] and "line" in en["text/plain"]
    assert subject_of(by_rcpt["vendor@partner.test"]).endswith(": Cloning round failed")
    assert message_from_bytes(by_rcpt["nadav@college.test"])["Content-Language"] == "he"


def test_two_rules_two_mails(conn, smtp):
    base = {"enabled": True, "account_id": "acc_test", "categories": ["cloning"], "min_severity": "err"}
    configure(conn, smtp, rules=[
        {**base, "id": "r1", "name": "", "recipients": [{"address": "a@college.test", "lang": "he"}]},
        {**base, "id": "r2", "name": "", "recipients": [{"address": "b@college.test", "lang": "he"}]}])
    assert ea.enqueue(conn, "round_failed", item("x"), now=T0) == 2
    ea.deliver_due(conn, None, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    assert sorted(m["rcpts"][0] for m in smtp.messages) == ["a@college.test", "b@college.test"]


def test_the_first_recipient_creates_the_default_rule_by_itself():
    doc = {"accounts": [{"id": "acc_1"}], "rules": []}
    rule, created = ec.add_recipient(doc, "nadav@college.test", "he")
    assert created is True and doc["rules"] == [rule]
    assert rule["categories"] == list(ec.CATEGORIES) and rule["min_severity"] == "warn"
    assert rule["recipients"] == [{"address": "nadav@college.test", "lang": "he"}]
    assert rule["account_id"] == "acc_1" and rule["enabled"] is True
    again, created2 = ec.add_recipient(doc, "it@college.test", "en")
    assert created2 is False and len(doc["rules"]) == 1
    assert [r["address"] for r in again["recipients"]] == ["nadav@college.test", "it@college.test"]


def test_direct_send_warns_about_recipients_outside_the_domain():
    doc = {"accounts": [{"id": "a", "provider": "o365_direct", "from_addr": "s@college.ac.il"}],
           "rules": [{"id": "r", "account_id": "a", "recipients": [
               {"address": "n@college.ac.il", "lang": "he"}, {"address": "n@gmail.example", "lang": "he"}]}]}
    warn = ec.warnings(doc)
    assert len(warn) == 1 and "n@gmail.example" in warn[0] and "n@college.ac.il" not in warn[0].split("אל:")[1]


# --- the queue -----------------------------------------------------------------

def test_events_of_one_kind_coalesce_into_one_mail_after_the_gather_delay(conn, smtp):
    configure(conn, smtp)
    ea.enqueue(conn, "round_failed", item("סבב 1"), now=T0)
    ea.enqueue(conn, "round_failed", item("סבב 2"), now=T0 + timedelta(seconds=10))
    assert len(queue(conn)) == 1
    ea.deliver_due(conn, None, now=T0 + timedelta(seconds=ea.GATHER_SECONDS - 1))
    assert smtp.messages == []
    ea.deliver_due(conn, None, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    assert len(smtp.messages) == 1
    body = body_of(smtp.messages[0]["raw"])
    assert "סבב 1" in body and "סבב 2" in body
    assert queue(conn)[0]["state"] == "sent" and len(events(conn, "email_alert_sent")) == 1


def test_the_same_kind_is_not_mailed_again_inside_the_rate_window(conn, smtp):
    configure(conn, smtp)
    ea.enqueue(conn, "round_failed", item("סבב 1"), now=T0)
    sent_at = T0 + timedelta(seconds=ea.GATHER_SECONDS)
    ea.deliver_due(conn, None, now=sent_at)
    ea.enqueue(conn, "round_failed", item("סבב 2"), now=sent_at + timedelta(seconds=5))
    ea.deliver_due(conn, None, now=sent_at + timedelta(seconds=ea.RATE_WINDOW_SECONDS - 1))
    assert len(smtp.messages) == 1
    ea.deliver_due(conn, None, now=sent_at + timedelta(seconds=ea.RATE_WINDOW_SECONDS))
    assert len(smtp.messages) == 2
    ea.enqueue(conn, "disk_red", item("דיסק"), now=sent_at)        # another kind: its own window
    ea.deliver_due(conn, None, now=sent_at + timedelta(seconds=ea.GATHER_SECONDS))
    assert "דיסק אדום" in subject_of(smtp.messages[-1]["raw"])


def test_a_failed_send_stays_queued_with_backoff_and_is_journaled(conn, smtp):
    configure(conn, smtp)
    smtp.data_code = 451
    ea.enqueue(conn, "round_failed", item("סבב 1"), now=T0)
    first = T0 + timedelta(seconds=ea.GATHER_SECONDS)
    ea.deliver_due(conn, None, now=first)
    row = queue(conn)[0]
    assert (row["state"], row["attempts"], row["last_code"]) == ("pending", 1, 451)
    assert row["next_at"] == (first + timedelta(seconds=ea.backoff_seconds(1))).isoformat()
    failed = events(conn, "email_alert_failed")
    assert len(failed) == 1 and "451" in failed[0]
    smtp.data_code = 250
    ea.deliver_due(conn, None, now=first + timedelta(seconds=ea.backoff_seconds(1) - 1))
    assert smtp.messages == []
    ea.deliver_due(conn, None, now=first + timedelta(seconds=ea.backoff_seconds(1)))
    assert len(smtp.messages) == 1 and queue(conn)[0]["state"] == "sent"


def test_after_max_attempts_the_mail_is_given_up_visibly(conn, smtp):
    configure(conn, smtp)
    smtp.data_code = 554
    ea.enqueue(conn, "round_failed", item("סבב 1"), now=T0)
    now = T0 + timedelta(seconds=ea.GATHER_SECONDS)
    for _ in range(ea.MAX_ATTEMPTS):
        ea.deliver_due(conn, None, now=now)
        now += timedelta(seconds=ea.BACKOFF_CAP_SECONDS)
    row = queue(conn)[0]
    assert (row["state"], row["attempts"]) == ("failed", ea.MAX_ATTEMPTS)
    assert len(events(conn, "email_alert_failed")) == ea.MAX_ATTEMPTS - 1
    assert len(events(conn, "email_alert_gave_up")) == 1


def test_an_event_arriving_while_a_mail_is_being_sent_is_not_lost(conn, smtp):
    configure(conn, smtp)
    ea.enqueue(conn, "round_failed", item("סבב 1"), now=T0)

    def failing_sender(acc, rcpts, msg, **kw):
        ea.enqueue(conn, "round_failed", item("סבב 2"), now=T0)   # arrives mid-send
        return ea.SendResult(False, 421, "busy")

    ea.deliver_due(conn, None, now=T0 + timedelta(seconds=ea.GATHER_SECONDS), sender=failing_sender)
    rows = [r for r in queue(conn) if r["state"] == "pending"]
    assert len(rows) == 1 and json.loads(rows[0]["items_json"]) == ["סבב 1", "סבב 2"]


def test_a_blocked_account_waits_without_spending_attempts(conn, smtp, tmp_path):
    configure(conn, smtp, auth="password", username="svc", security="none")   # no password stored
    data = tmp_path / "data"
    ea.enqueue(conn, "round_failed", item("x"), now=T0)
    ea.deliver_due(conn, data, now=T0 + timedelta(hours=2))
    row = queue(conn)[0]
    assert (row["state"], row["attempts"]) == ("pending", 0)
    assert smtp.commands == []


def test_a_mail_for_a_deleted_rule_is_closed_visibly(conn, smtp):
    doc = configure(conn, smtp)
    ea.enqueue(conn, "round_failed", item("x"), now=T0)
    doc["rules"] = []
    ec.save(conn, doc)
    ea.deliver_due(conn, None, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    row = queue(conn)[0]
    assert row["state"] == "failed" and "הכלל" in row["last_error"]


def test_the_finished_rows_are_capped(conn, smtp, monkeypatch):
    configure(conn, smtp)
    monkeypatch.setattr(ea, "KEEP_FINISHED", 3)
    for _ in range(6):
        conn.execute("INSERT INTO email_queue (rule_id, kind, lang, items_json, created_at, next_at,"
                     " state) VALUES ('rule_all', 'round_failed', 'he', '[]', ?, ?, 'sent')",
                     (T0.isoformat(),) * 2)
    conn.commit()
    ea.deliver_due(conn, None, now=T0)
    assert len(queue(conn)) == 3


# --- OAuth device code ----------------------------------------------------------

class FakeIdP:
    """Microsoft/Google, faked: answers by URL, records the requests."""

    def __init__(self):
        self.calls = []
        self.token_answers = []

    def __call__(self, url, fields):
        self.calls.append((url, dict(fields)))
        if url.endswith("/devicecode") or url.endswith("/device/code"):
            return 200, {"device_code": "dev-secret", "user_code": "ABCD-1234",
                         "verification_uri": "https://microsoft.com/devicelogin",
                         "expires_in": 900, "interval": 5, "message": "go"}
        return self.token_answers.pop(0) if self.token_answers else (400, {"error": "authorization_pending"})


def oauth_account(fake, **over):
    return acc_for(fake, **{"provider": "office365", "auth": "oauth", "security": "starttls",
                            "username": "alerts@college.test", "oauth_client_id": "app-1", **over})


def test_device_code_start_poll_and_the_token_lands_in_a_0600_file(tmp_path, smtp):
    data = tmp_path / "data"
    data.mkdir()
    idp = FakeIdP()
    flows = eo.DeviceFlows(http=idp)
    acc = oauth_account(smtp, id="acc_o1")
    started = flows.start(acc, data)
    assert (started["user_code"], started["verification_uri"]) == ("ABCD-1234", "https://microsoft.com/devicelogin")
    assert "device_code" not in started
    assert idp.calls[0][0] == "https://login.microsoftonline.com/organizations/oauth2/v2.0/devicecode"
    assert "SMTP.Send" in idp.calls[0][1]["scope"]
    assert flows.poll(acc, data)["state"] == "pending"
    idp.token_answers.append((200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}))
    assert flows.poll(acc, data)["state"] == "connected"
    assert idp.calls[-1][1]["grant_type"] == eo.DEVICE_GRANT and idp.calls[-1][1]["device_code"] == "dev-secret"
    assert ec.read_oauth(data, "acc_o1")["refresh_token"] == "rt-1"
    if os.name == "posix":
        assert (ec.secret_path(data, "acc_o1", "oauth").stat().st_mode & 0o777) == 0o600
    eo.TOKENS.drop("acc_o1")


def test_a_refused_login_is_said_with_the_providers_reason(tmp_path, smtp):
    data = tmp_path / "data"
    data.mkdir()
    idp = FakeIdP()
    flows = eo.DeviceFlows(http=idp)
    acc = oauth_account(smtp, id="acc_o2")
    flows.start(acc, data)
    idp.token_answers.append((400, {"error": "invalid_scope", "error_description": "scope not allowed"}))
    result = flows.poll(acc, data)
    assert result["state"] == "error" and "invalid_scope" in result["message"]
    assert ec.read_oauth(data, "acc_o2") == {}


def test_no_internet_fails_at_the_first_step_visibly(tmp_path, smtp):
    def offline(url, fields):
        raise eo.OAuthError("אין חיבור ל-login.microsoftonline.com")
    with pytest.raises(eo.OAuthError, match="אין חיבור"):
        eo.DeviceFlows(http=offline).start(oauth_account(smtp, id="acc_o3"), tmp_path)


def test_no_client_id_is_refused_before_any_request(tmp_path, smtp):
    idp = FakeIdP()
    with pytest.raises(eo.OAuthError, match="client id"):
        eo.DeviceFlows(http=idp).start(oauth_account(smtp, id="acc_o4", oauth_client_id=""), tmp_path)
    assert idp.calls == []


@pytest.mark.parametrize("tenant", ["evil.test/../x", "x?a=b", "evil.test#frag", "a/b"])
def test_a_bad_tenant_stored_by_hand_never_reaches_the_network(tmp_path, smtp, tenant):
    """Security review (SSRF): even a tenant written straight into the DB is refused
    before any request -- the URL is never built from it."""
    idp = FakeIdP()
    with pytest.raises(eo.OAuthError, match="tenant"):
        eo.DeviceFlows(http=idp).start(oauth_account(smtp, id="acc_t1", oauth_tenant=tenant), tmp_path)
    assert idp.calls == []


def test_every_request_goes_only_to_the_providers_own_host(tmp_path, smtp, monkeypatch):
    idp = FakeIdP()
    monkeypatch.setitem(eo.OAUTH, "microsoft", {**eo.OAUTH["microsoft"],
                        "device": "https://evil.test/{tenant}/oauth2/v2.0/devicecode"})
    with pytest.raises(eo.OAuthError, match="המארח הצפוי"):
        eo.DeviceFlows(http=idp).start(oauth_account(smtp, id="acc_t2"), tmp_path)
    assert idp.calls == []


def test_the_tenant_is_quoted_into_the_path(tmp_path, smtp):
    idp = FakeIdP()
    eo.DeviceFlows(http=idp).start(oauth_account(smtp, id="acc_t3", oauth_tenant="college.ac.il"), tmp_path)
    assert idp.calls[0][0] == "https://login.microsoftonline.com/college.ac.il/oauth2/v2.0/devicecode"


def test_sending_refreshes_the_token_rotates_it_and_uses_xoauth2(conn, smtp, tmp_path):
    data = tmp_path / "data"
    # saved straight to the DB: the fake SMTP has no TLS, and merge_account refuses oauth without it
    acc = oauth_account(smtp, id="acc_o5", security="none")
    ec.save(conn, {"accounts": [acc], "rules": [{"id": "r", "name": "", "enabled": True,
                   "account_id": "acc_o5", "categories": ["cloning"], "min_severity": "err",
                   "recipients": [{"address": "n@college.test", "lang": "he"}]}]})
    eo.save_token(data, "acc_o5", "rt-old", None)
    eo.TOKENS.drop("acc_o5")
    calls = []

    def idp(url, fields):
        calls.append(fields)
        return 200, {"access_token": "at-fresh", "refresh_token": "rt-new", "expires_in": 3600}
    ea.enqueue(conn, "round_failed", item("x"), now=T0)
    ea.deliver_due(conn, data, now=T0 + timedelta(seconds=ea.GATHER_SECONDS), http=idp)
    assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "rt-old"
    assert ec.read_oauth(data, "acc_o5")["refresh_token"] == "rt-new"
    assert smtp.auth == ["user=alerts@college.test\x01auth=Bearer at-fresh\x01\x01"]
    assert queue(conn)[0]["state"] == "sent"
    eo.TOKENS.drop("acc_o5")


def test_a_failed_refresh_fails_the_send_with_the_reason(conn, smtp, tmp_path):
    data = tmp_path / "data"
    acc = oauth_account(smtp, id="acc_o6", security="none")
    ec.save(conn, {"accounts": [acc], "rules": [{"id": "r", "name": "", "enabled": True,
                   "account_id": "acc_o6", "categories": ["cloning"], "min_severity": "err",
                   "recipients": [{"address": "n@college.test", "lang": "he"}]}]})
    eo.save_token(data, "acc_o6", "rt-old", None)
    eo.TOKENS.drop("acc_o6")
    ea.enqueue(conn, "round_failed", item("x"), now=T0)
    ea.deliver_due(conn, data, now=T0 + timedelta(seconds=ea.GATHER_SECONDS),
                   http=lambda url, f: (400, {"error": "invalid_grant"}))
    row = queue(conn)[0]
    assert row["state"] == "pending" and row["attempts"] == 1 and "invalid_grant" in row["last_error"]
    assert smtp.commands == []


# --- the event sources ---------------------------------------------------------

def watcher(conn, tmp_path, now=T0):
    clock = {"now": now}
    w = ea.Watcher(conn, tmp_path / "data", now=lambda: clock["now"])
    return w, clock


def test_the_journal_tail_turns_a_failed_round_into_a_mail(conn, tmp_path, smtp):
    configure(conn, smtp)
    journal(conn, "room_failed", "old_round written=0/3")      # before the first run: history
    w, clock = watcher(conn, tmp_path)
    w.run_once()
    assert [r["kind"] for r in queue(conn)] == ["server_started"]
    journal(conn, "room_failed", "room_1 wave_1 written=1/3")
    journal(conn, "login", "noc")
    journal(conn, "auto_reboot", "נדחה — יש סבב חדר פתוח או רץ")   # a deferral is not an alert
    w.run_once()
    rows = {r["kind"]: json.loads(r["items_json"]) for r in queue(conn)}
    assert set(rows) == {"server_started", "round_failed"}
    assert len(rows["round_failed"]) == 1 and "room_1 wave_1" in rows["round_failed"][0]
    clock["now"] = T0 + timedelta(seconds=ea.GATHER_SECONDS)
    w.run_once()
    assert len(smtp.messages) == 2


def test_the_journal_events_that_become_alerts(conn, tmp_path, smtp):
    configure(conn, smtp)
    w, _ = watcher(conn, tmp_path)
    w.run_once()
    for event, detail in (("update_apply_done", "v1.0.1"), ("update_apply_failed", "v1.0.2"),
                          ("auto_reboot", "השרת אותחל לפי לוח הזמנים"),
                          ("disk_failure", "aa:bb sda"), ("low_space", "9GB"),
                          ("peer_unreachable", "branch-2"), ("update_available", "v1 → v2")):
        journal(conn, event, detail)
    w.run_once()
    assert {r["kind"] for r in queue(conn)} == {
        "server_started", "update_installed", "update_failed", "auto_reboot", "disk_red",
        "low_space", "peer", "update_available"}


def test_the_restart_mail_says_since_when_the_server_was_down(conn, tmp_path, smtp):
    data = tmp_path / "data"
    ea.write_heartbeat(data, T0 - timedelta(hours=2, minutes=5))
    w, _ = watcher(conn, tmp_path)
    w.run_once()
    started = events(conn, "server_started")
    assert len(started) == 1 and "2 שעות ו-5 דקות" in started[0]
    assert ea.local_text(conn, T0 - timedelta(hours=2, minutes=5)) in started[0]
    assert ea.read_heartbeat(data) == (T0, None)
    w.run_once()
    assert len(events(conn, "server_started")) == 1


def test_an_unreadable_heartbeat_is_said_not_folded_into_unknown(conn, tmp_path, smtp):
    (tmp_path / "data" / ea.HEARTBEAT_FILE).write_text("garbage", encoding="ascii")
    w, _ = watcher(conn, tmp_path)
    w.run_once()
    assert "לא נקרא" in events(conn, "server_started")[0]


def test_an_available_update_is_announced_once_per_version(conn, tmp_path, smtp):
    from server import update
    w, _ = watcher(conn, tmp_path)
    w.run_once()
    update.record_last_check(conn, {"current": "v1.0.0", "latest": "v1.0.1", "available": True})
    w.run_once()
    w.run_once()
    assert events(conn, "update_available") == ["v1.0.0 → v1.0.1"]
    update.record_last_check(conn, {"current": "v1.0.0", "latest": "v1.0.2", "available": True})
    w.run_once()
    assert len(events(conn, "update_available")) == 2


def _node(conn, *, seen, failed, error="timed out"):
    conn.execute("INSERT OR REPLACE INTO storage_nodes (id, label, base_url, credential_ref,"
                 " enrolled_at, last_seen_at, last_error, last_error_at)"
                 " VALUES ('n1', 'branch-2', 'https://10.0.0.2:8443', 'x', ?, ?, ?, ?)",
                 ((T0 - timedelta(days=1)).isoformat(), seen and seen.isoformat(), error,
                  failed and failed.isoformat()))
    conn.commit()


def test_a_secondary_that_stops_answering_and_comes_back(conn, tmp_path, smtp):
    w, clock = watcher(conn, tmp_path)
    _node(conn, seen=T0 - timedelta(minutes=3), failed=T0)
    w.run_once()
    assert events(conn, "peer_unreachable") == []
    clock["now"] = T0 + timedelta(minutes=20)
    _node(conn, seen=T0 - timedelta(minutes=3), failed=clock["now"])
    w.run_once()
    w.run_once()
    down = events(conn, "peer_unreachable")
    assert len(down) == 1 and "branch-2" in down[0] and "timed out" in down[0]
    _node(conn, seen=clock["now"], failed=None, error=None)
    w.run_once()
    assert len(events(conn, "peer_back")) == 1


def test_a_secondary_never_checked_is_not_called_down(conn, tmp_path, smtp):
    w, clock = watcher(conn, tmp_path)
    _node(conn, seen=None, failed=None, error=None)
    clock["now"] = T0 + timedelta(days=3)
    w.run_once()
    assert events(conn, "peer_unreachable") == []


def test_on_a_secondary_a_silent_primary_is_an_alert(conn, tmp_path, smtp):
    from server.interserver import storage_nodes
    set_setting(conn, storage_nodes.ROLE_KEY, storage_nodes.ROLE_SECONDARY)
    set_setting(conn, storage_nodes.PRIMARY_URL_KEY, "https://10.0.0.1:8443")
    conn.execute("INSERT INTO parent_credentials (singleton, parent_id, token_hash, bound_cert_ref,"
                 " pinned_parent_spki, protocol_version, enrolled_at, last_contact_at)"
                 " VALUES (1, 'p', x'00', 'c', 's', '1', ?, ?)",
                 (T0.isoformat(), (T0 - timedelta(minutes=30)).isoformat()))
    conn.commit()
    w, _ = watcher(conn, tmp_path)
    w.run_once()
    assert len(events(conn, "peer_unreachable")) == 1


def test_a_failing_step_is_journaled_once_and_the_rest_still_runs(conn, tmp_path, smtp, monkeypatch):
    w, clock = watcher(conn, tmp_path)
    monkeypatch.setattr(ea, "_peer_status", lambda conn, now: 1 / 0)
    w.run_once()
    clock["now"] = T0 + timedelta(minutes=5)
    w.run_once()
    assert len(events(conn, "email_alerts_step_failed")) == 1
    assert ea.read_heartbeat(tmp_path / "data") == (clock["now"], None)


# --- the API -------------------------------------------------------------------

API = "/api/console/email-alerts"


@pytest.fixture()
def api(server, smtp):
    return server, smtp


def _relay(server, smtp, **extra):
    r = server["admin"].post(f"{API}/accounts", json={
        "provider": "relay", "host": "127.0.0.1", "port": smtp.port,
        "from_addr": "imagesrv@college.test", **extra})
    assert r.status_code == 200, r.text
    return r.json()["account_id"]


@pytest.mark.parametrize("method, path", [
    ("get", ""), ("get", "/history"), ("post", "/accounts"), ("put", "/accounts/acc_x"),
    ("delete", "/accounts/acc_x"), ("post", "/accounts/acc_x/test"),
    ("post", "/accounts/acc_x/oauth/start"), ("post", "/accounts/acc_x/oauth/poll"),
    ("delete", "/accounts/acc_x/oauth"), ("post", "/recipients"), ("post", "/rules"),
    ("put", "/rules/r"), ("delete", "/rules/r")])
def test_every_route_is_admin_only(api, method, path):
    server, _ = api
    call = lambda client: getattr(client, method)(API + path, **({"json": {}} if method in ("post", "put") else {}))
    assert call(server["deploy"]).status_code == 403
    assert call(server["anon"]).status_code == 401


def test_the_short_path_account_recipient_test(api):
    server, smtp = api
    acc_id = _relay(server, smtp)
    r = server["admin"].post(f"{API}/recipients", json={"address": "nadav@college.test"})
    assert r.status_code == 200 and r.json()["rule_created"] is True
    rule = r.json()["rules"][0]
    assert rule["recipients"] == [{"address": "nadav@college.test", "lang": "he"}]
    assert rule["min_severity"] == "warn" and rule["categories"] == list(ec.CATEGORIES)
    test = server["admin"].post(f"{API}/accounts/{acc_id}/test", json={"to": "nadav@college.test"}).json()
    assert (test["ok"], test["code"]) == (True, 250)
    assert "בדיקה" in body_of(smtp.messages[0]["raw"])
    smtp.data_code = 554
    bad = server["admin"].post(f"{API}/accounts/{acc_id}/test", json={"to": "nadav@college.test"}).json()
    assert (bad["ok"], bad["code"]) == (False, 554)
    assert len(events(server["ctx"].conn, "email_test")) == 2


def test_a_recipient_before_any_account_is_refused(api):
    server, _ = api
    assert server["admin"].post(f"{API}/recipients", json={"address": "n@college.test"}).status_code == 409


def test_the_password_is_stored_aside_and_never_returned(api):
    server, smtp = api
    secret = "Sup3r-Secret-Pw"
    r = server["admin"].post(f"{API}/accounts", json={
        "provider": "custom", "host": "127.0.0.1", "port": smtp.port, "security": "starttls",
        "auth": "password", "username": "svc@college.test", "password": secret,
        "from_addr": "imagesrv@college.test"})
    assert r.status_code == 200, r.text
    acc_id = r.json()["account_id"]
    got = server["admin"].get(API)
    for text in (r.text, got.text):
        assert secret not in text
    assert got.json()["accounts"][0]["password_set"] is True
    conn = server["ctx"].conn
    assert secret not in (get_setting(conn, ec.SETTING) or "")
    assert not any(secret in x["detail"] for x in conn.execute("SELECT detail FROM journal"))
    assert ec.read_secret(server["ctx"].data_dir, acc_id, "password") == secret


def test_retargeting_an_account_without_a_password_deletes_it_and_blocks_sending(api):
    server, smtp = api
    r = server["admin"].post(f"{API}/accounts", json={
        "provider": "custom", "host": "127.0.0.1", "port": smtp.port, "security": "starttls",
        "auth": "password", "username": "svc", "password": "Stored-Pw", "from_addr": "s@college.test"})
    acc_id = r.json()["account_id"]
    view = server["admin"].put(f"{API}/accounts/{acc_id}", json={"host": "localhost"}).json()
    acc = view["accounts"][0]
    assert acc["password_set"] is False and acc["blocked"]
    assert len(events(server["ctx"].conn, "email_alerts_password_cleared")) == 1
    assert server["admin"].post(f"{API}/accounts/{acc_id}/test",
                                json={"to": "n@college.test"}).status_code == 409
    assert smtp.commands == [] and smtp.auth == []


def test_an_account_used_by_a_rule_cannot_be_deleted(api):
    server, smtp = api
    acc_id = _relay(server, smtp)
    server["admin"].post(f"{API}/recipients", json={"address": "n@college.test"})
    assert server["admin"].delete(f"{API}/accounts/{acc_id}").status_code == 409


def test_rules_crud_and_validation(api):
    server, smtp = api
    acc_id = _relay(server, smtp)
    r = server["admin"].post(f"{API}/rules", json={
        "name": "דיסקים לטכנאי", "categories": ["disks"], "min_severity": "err",
        "recipients": [{"address": "tech@college.test", "lang": "en"}]})
    assert r.status_code == 200, r.text
    rule_id = r.json()["rule_id"]
    assert r.json()["rules"][0]["account_id"] == acc_id
    upd = server["admin"].put(f"{API}/rules/{rule_id}", json={"enabled": False}).json()
    assert upd["rules"][0]["enabled"] is False
    for body in ({"categories": ["nope"]}, {"min_severity": "loud"},
                 {"recipients": [{"address": "x@college.test", "lang": "fr"}]}, {"account_id": "acc_x"}):
        assert server["admin"].put(f"{API}/rules/{rule_id}", json=body).status_code == 400, body
    assert server["admin"].delete(f"{API}/rules/{rule_id}").json()["rules"] == []


def test_oauth_routes_start_and_poll_without_leaking_the_device_code(api):
    server, smtp = api
    idp = FakeIdP()
    server["ctx"].email_http = idp
    r = server["admin"].post(f"{API}/accounts", json={"provider": "office365", "username": "alerts@college.test",
                                                       "from_addr": "alerts@college.test",
                                                       "oauth_client_id": "app-1"})
    acc_id = r.json()["account_id"]
    started = server["admin"].post(f"{API}/accounts/{acc_id}/oauth/start")
    assert started.status_code == 200 and started.json()["user_code"] == "ABCD-1234"
    assert "dev-secret" not in started.text
    assert server["admin"].post(f"{API}/accounts/{acc_id}/oauth/poll").json()["state"] == "pending"
    idp.token_answers.append((200, {"access_token": "at", "refresh_token": "rt-secret", "expires_in": 3600}))
    assert server["admin"].post(f"{API}/accounts/{acc_id}/oauth/poll").json()["state"] == "connected"
    view = server["admin"].get(API)
    assert view.json()["accounts"][0]["oauth_connected"] is True
    assert "rt-secret" not in view.text and "dev-secret" not in view.text
    assert len(events(server["ctx"].conn, "email_oauth_connected")) == 1
    eo.TOKENS.drop(acc_id)


def test_retargeting_a_connected_oauth_account_disconnects_it(api):
    server, smtp = api
    idp = FakeIdP()
    server["ctx"].email_http = idp
    acc_id = server["admin"].post(f"{API}/accounts", json={
        "provider": "office365", "username": "alerts@college.test", "from_addr": "alerts@college.test",
        "oauth_client_id": "app-1"}).json()["account_id"]
    server["admin"].post(f"{API}/accounts/{acc_id}/oauth/start")
    idp.token_answers.append((200, {"access_token": "at-live", "refresh_token": "rt", "expires_in": 3600}))
    assert server["admin"].post(f"{API}/accounts/{acc_id}/oauth/poll").json()["state"] == "connected"
    assert eo.TOKENS.get(acc_id) == "at-live"
    # a password does not launder the retarget of an OAuth account
    assert server["admin"].put(f"{API}/accounts/{acc_id}",
                               json={"host": "evil.test", "password": "x"}).status_code == 400
    assert eo.TOKENS.get(acc_id) == "at-live"          # refused: nothing changed
    view = server["admin"].put(f"{API}/accounts/{acc_id}", json={"host": "evil.test"}).json()
    acc = view["accounts"][0]
    assert acc["oauth_connected"] is False and acc["blocked"]
    assert eo.TOKENS.get(acc_id) is None
    assert not ec.secret_path(server["ctx"].data_dir, acc_id, "oauth").exists()


def _oauth_started(server):
    idp = FakeIdP()
    server["ctx"].email_http = idp
    acc_id = server["admin"].post(f"{API}/accounts", json={
        "provider": "office365", "username": "alerts@college.test", "from_addr": "alerts@college.test",
        "oauth_client_id": "app-1"}).json()["account_id"]
    assert server["admin"].post(f"{API}/accounts/{acc_id}/oauth/start").status_code == 200
    # the operator approves in the browser: the next poll would get a token
    idp.token_answers.append((200, {"access_token": "at-late", "refresh_token": "rt-late", "expires_in": 3600}))
    return idp, acc_id


@pytest.mark.parametrize("interrupt", ["disconnect", "retarget", "delete"])
def test_a_login_started_before_a_change_never_saves_a_token(api, interrupt):
    """Security review: a device-code login that started before a disconnect, a retarget
    or a delete must not land a refresh token on the changed account."""
    server, _ = api
    idp, acc_id = _oauth_started(server)
    if interrupt == "disconnect":
        server["admin"].delete(f"{API}/accounts/{acc_id}/oauth")
    elif interrupt == "retarget":
        assert server["admin"].put(f"{API}/accounts/{acc_id}", json={"host": "evil.test"}).status_code == 200
    else:
        assert server["admin"].delete(f"{API}/accounts/{acc_id}").status_code == 200
    poll = server["admin"].post(f"{API}/accounts/{acc_id}/oauth/poll")
    assert poll.status_code in (200, 404)
    if poll.status_code == 200:
        assert poll.json()["state"] != "connected"
    assert not ec.secret_path(server["ctx"].data_dir, acc_id, "oauth").exists()
    assert eo.TOKENS.get(acc_id) is None
    assert not any(f.get("device_code") for _, f in idp.calls[1:])   # never asked for the token


def test_poll_refuses_when_the_target_changed_even_without_cancel(tmp_path, smtp):
    """The second guard alone: a fingerprint taken at start, compared at poll."""
    data = tmp_path / "data"
    data.mkdir()
    idp = FakeIdP()
    flows = eo.DeviceFlows(http=idp)
    acc = oauth_account(smtp, id="acc_fp")
    flows.start(acc, data)
    idp.token_answers.append((200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}))
    moved = {**acc, "host": "evil.test"}
    assert flows.poll(moved, data)["state"] == "cancelled"
    assert ec.read_oauth(data, "acc_fp") == {} and len(idp.calls) == 1
    assert flows.poll(acc, data)["state"] == "idle"          # the login is gone, not resumable


def test_a_cancel_during_the_providers_answer_wins(tmp_path, smtp):
    """The race: cancel() lands while the token request is in flight -- nothing is saved."""
    data = tmp_path / "data"
    data.mkdir()
    flows = None

    def idp(url, fields):
        if "device_code" in fields:
            flows.cancel("acc_race")                         # the operator retargets right now
            return 200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        return 200, {"device_code": "d", "user_code": "U", "verification_uri": "https://microsoft.com/devicelogin"}
    flows = eo.DeviceFlows(http=idp)
    acc = oauth_account(smtp, id="acc_race")
    flows.start(acc, data)
    assert flows.poll(acc, data)["state"] == "cancelled"
    assert ec.read_oauth(data, "acc_race") == {} and eo.TOKENS.get("acc_race") is None


def test_a_cancel_during_start_stops_the_login(tmp_path, smtp):
    """Security review: cancel() that lands while start() waits for the provider --
    start does not open the login afterwards, and says so."""
    flows = None

    def idp(url, fields):
        flows.cancel("acc_s1")                               # retarget/delete/disconnect, right now
        return 200, {"device_code": "d", "user_code": "U", "verification_uri": "https://microsoft.com/devicelogin"}
    flows = eo.DeviceFlows(http=idp)
    with pytest.raises(eo.OAuthError, match="בוטלה"):
        flows.start(oauth_account(smtp, id="acc_s1"), tmp_path)
    assert flows.is_open("acc_s1") is False
    assert flows.poll(oauth_account(smtp, id="acc_s1"), tmp_path)["state"] == "idle"


def test_a_retarget_cancels_the_login_even_if_changed_back(api):
    """cancel() on every retarget -- not only the fingerprint check: A → B → A kills it too."""
    server, _ = api
    idp, acc_id = _oauth_started(server)
    server["admin"].put(f"{API}/accounts/{acc_id}", json={"host": "evil.test"})
    server["admin"].put(f"{API}/accounts/{acc_id}", json={"host": "smtp.office365.com"})
    assert server["admin"].get(API).json()["accounts"][0]["oauth_pending"] is False
    assert server["admin"].post(f"{API}/accounts/{acc_id}/oauth/poll").json()["state"] != "connected"
    assert not ec.secret_path(server["ctx"].data_dir, acc_id, "oauth").exists()


def test_a_deleted_account_leaves_no_open_login(api):
    server, _ = api
    _, acc_id = _oauth_started(server)
    flows = server["ctx"].email_oauth_flows
    assert flows.is_open(acc_id) is True
    assert server["admin"].delete(f"{API}/accounts/{acc_id}").status_code == 200
    assert flows.is_open(acc_id) is False


def test_the_history_shows_sent_failed_pending_with_code_time_and_rule(api):
    server, smtp = api
    conn, data_dir = server["ctx"].conn, server["ctx"].data_dir
    _relay(server, smtp)
    server["admin"].post(f"{API}/recipients", json={"address": "n@college.test"})
    ea.enqueue(conn, "round_failed", item("סבב 1"), now=T0)
    ea.deliver_due(conn, data_dir, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    smtp.data_code = 451
    ea.enqueue(conn, "disk_red", item("דיסק"), now=T0)
    ea.deliver_due(conn, data_dir, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    h = server["admin"].get(f"{API}/history").json()
    by_kind = {q["kind"]: q for q in h["queue"]}
    assert by_kind["round_failed"]["state"] == "sent" and by_kind["round_failed"]["code"] == 250
    assert by_kind["round_failed"]["sent_at"] and by_kind["round_failed"]["rule_name"] == "ברירת מחדל"
    assert by_kind["disk_red"]["state"] == "pending" and by_kind["disk_red"]["code"] == 451
    assert by_kind["disk_red"]["attempts"] == 1 and by_kind["disk_red"]["recipients"] == ["n@college.test"]
    assert {"email_alert_sent", "email_alert_failed"} <= {j["event"] for j in h["journal"]}


def test_the_view_lists_providers_categories_and_languages(api):
    server, _ = api
    v = server["admin"].get(API).json()
    assert [p["key"] for p in v["providers"]] == ["office365", "o365_direct", "relay", "gmail", "custom"]
    assert [c["key"] for c in v["categories"]] == ["cloning", "disks", "updates", "system", "peers"]
    assert v["languages"] == ["he", "en"] and v["default_language"] == "he"
    assert {k["key"]: k["severity"] for k in v["kinds"]}["round_failed"] == "err"


def test_the_server_starts_the_alert_thread_and_its_heartbeat(tmp_path, images_root, clock):
    from fastapi.testclient import TestClient
    from server.app import create_app
    from test_sender import Recorder
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), health_hooks={"interfaces": lambda: []})
    beat = tmp_path / "data" / ea.HEARTBEAT_FILE
    with TestClient(app):
        deadline = time.monotonic() + 10
        while not beat.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert beat.exists() and app.state.ctx.email_alerts.running
    assert not app.state.ctx.email_alerts.running



# --- security (31/10): no secret in the journal, the log or a console answer ----

def _leaks(secrets, places):
    return [(secret, where) for secret in secrets for where, text in places if secret in text]


def _journal_text(conn) -> str:
    return "\n".join(repr(tuple(r)) for r in conn.execute("SELECT * FROM journal"))


def test_no_secret_reaches_the_journal_the_log_or_any_console_answer(api, caplog):
    """#1751 security, 31/10: the passwords, the client secret, the device code and
    both tokens never land in the journal, the Python log, the stored settings or any
    console answer -- on the way in, on success and on failure. The secrets did reach
    the server (positive evidence below), so their absence is not an empty test."""
    caplog.set_level(logging.DEBUG)
    server, smtp = api
    admin, conn, data = server["admin"], server["ctx"].conn, server["ctx"].data_dir
    pw1, pw2, cs, rt, at = "Pw-Alpha-7731", "Pw-Beta-7732", "cs-Gamma-7733", "rt-Delta-7734", "at-Eps-7735"
    answers = []

    def call(method, path="", **kw):
        r = getattr(admin, method)(API + path, **kw)
        answers.append(r.text)
        return r

    acc_pw = call("post", "/accounts", json={
        "provider": "custom", "host": "127.0.0.1", "port": smtp.port, "security": "starttls",
        "auth": "password", "username": "svc@college.test", "password": pw1,
        "from_addr": "imagesrv@college.test"}).json()["account_id"]
    assert call("put", f"/accounts/{acc_pw}", json={"password": pw2}).status_code == 200
    failed = call("post", f"/accounts/{acc_pw}/test", json={"to": "n@college.test"}).json()
    assert failed["ok"] is False                                 # no STARTTLS -> refused, said
    assert ec.read_secret(data, acc_pw, "password") == pw2

    idp = FakeIdP()
    server["ctx"].email_http = idp
    acc_o = call("post", "/accounts", json={
        "provider": "office365", "username": "alerts@college.test", "from_addr": "alerts@college.test",
        "oauth_client_id": "app-1", "oauth_client_secret": cs}).json()["account_id"]
    assert call("post", f"/accounts/{acc_o}/oauth/start").status_code == 200
    assert call("post", f"/accounts/{acc_o}/oauth/poll").json()["state"] == "pending"
    idp.token_answers.append((200, {"access_token": at, "refresh_token": rt, "expires_in": 3600}))
    assert call("post", f"/accounts/{acc_o}/oauth/poll").json()["state"] == "connected"
    assert call("post", f"/accounts/{acc_o}/test", json={"to": "n@college.test"}).json()["ok"] is False
    assert call("post", f"/accounts/{acc_o}/oauth/start").status_code == 200
    idp.token_answers.append((400, {"error": "access_denied", "error_description": "user said no"}))
    assert call("post", f"/accounts/{acc_o}/oauth/poll").json()["state"] == "error"
    call("get")
    call("get", "/history")
    # positive evidence: every secret was really in play
    assert ec.read_oauth(data, acc_o) == {"refresh_token": rt, "client_secret": cs}
    assert eo.TOKENS.get(acc_o) == at
    assert any(fields.get("client_secret") == cs and fields.get("device_code") == "dev-secret"
               for _, fields in idp.calls)
    call("delete", f"/accounts/{acc_o}/oauth")
    assert events(conn, "email_oauth_failed") and events(conn, "email_test")

    places = [("journal", _journal_text(conn)), ("log", caplog.text),
              ("settings", get_setting(conn, ec.SETTING) or "")]
    places += [(f"answer {i}", text) for i, text in enumerate(answers)]
    assert _leaks((pw1, pw2, cs, "dev-secret", rt, at), places) == []


def test_a_refused_login_in_the_queue_leaks_no_secret(conn, smtp, tmp_path, caplog):
    """The queue path, where the secret really goes on the wire (the fake has no TLS,
    so the accounts are saved straight to the DB): the server refuses the login, and
    neither the journal, the history nor the log carries the password or the token."""
    caplog.set_level(logging.DEBUG)
    data = tmp_path / "data"
    pw, at = "Pw-Queue-8841", "at-Queue-8842"
    rule = {"name": "", "enabled": True, "categories": ["cloning"], "min_severity": "err",
            "recipients": [{"address": "n@college.test", "lang": "he"}]}
    ec.save(conn, {"accounts": [acc_for(smtp, id="acc_p", auth="password", username="svc"),
                                oauth_account(smtp, id="acc_q", security="none")],
                   "rules": [{**rule, "id": "rp", "account_id": "acc_p"},
                             {**rule, "id": "rq", "account_id": "acc_q"}]})
    ec.write_secret(data, "acc_p", "password", pw)
    eo.save_token(data, "acc_q", "rt-Queue-8843", None)
    eo.TOKENS.put("acc_q", at, 3600)
    smtp.auth_code = 535
    ea.enqueue(conn, "round_failed", item("x"), now=T0)
    results = ea.deliver_due(conn, data, now=T0 + timedelta(seconds=ea.GATHER_SECONDS))
    eo.TOKENS.drop("acc_q")
    assert [r.code for r in results] == [535, 535]
    assert any(pw in a for a in smtp.auth) and any(at in a for a in smtp.auth)   # on the wire
    places = [("journal", _journal_text(conn)), ("log", caplog.text),
              ("history", json.dumps(ea.history(conn), ensure_ascii=False)),
              ("queue", repr([tuple(r) for r in queue(conn)]))]
    assert _leaks((pw, at, "rt-Queue-8843"), places) == []


# --- the text catalog -------------------------------------------------------------

def test_every_mail_text_has_a_key_in_both_languages():
    from server.system import email_text
    he, en = email_text.CATALOG["he"], email_text.CATALOG["en"]
    assert set(he) == set(en)
    for kind in ec.KINDS + ("test",):
        assert f"kind.{kind}" in he
    for cat in ec.CATEGORIES:
        assert f"cat.{cat}" in he
    for event in ea.JOURNAL_KINDS:
        assert f"item.{event}" in he


def test_every_kind_has_a_severity_and_a_category():
    assert set(ec.KIND_SEVERITY) == set(ec.KINDS) == set(ec.KIND_CATEGORY)
