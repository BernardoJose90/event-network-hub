"""Delivers queued notifications (browser push, Slack, email) and runs the scheduled checks
(help desk SLA warnings and escalations, ICT request deadline reminders).

Failures are retried with exponential backoff, then marked failed. A push subscription the push
service reports as gone (404/410) is deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from .. import db
from . import service

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
POLL_SECONDS = 5
SLA_WARN_MINUTES = 4  # P1/P2 not acknowledged after this long: warn the team (SLA is 5 minutes)
REMINDER_DAYS = (7, 1)  # ICT request deadline reminders to team contacts


class Sender:
    """The outbound channels, built from Settings. Tests replace the send_* methods."""

    def __init__(self, settings, vapid=None) -> None:
        self.settings = settings
        self.vapid = vapid  # py_vapid.Vapid with the private key

    def send_push(self, sub: dict[str, Any], payload: dict[str, Any], urgent: bool) -> None:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload),
                vapid_private_key=self.vapid,
                vapid_claims={"sub": self.settings.vapid_subject},
                ttl=3600 if urgent else 24 * 3600,
                headers={"Urgency": "high" if urgent else "normal"},
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            raise DeliveryError(str(exc)[:300], gone=status in (404, 410)) from exc

    def send_slack(self, payload: dict[str, Any]) -> None:
        response = httpx.post(self.settings.slack_webhook_url, json=payload, timeout=10)
        if response.status_code != 200:
            raise DeliveryError(f"Slack answered HTTP {response.status_code}: {response.text[:200]}")

    def send_email(self, to: str, subject: str, text: str) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg["Auto-Submitted"] = "auto-generated"  # RFC 3834: no auto-replies to this
        msg.set_content(text)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)


class DeliveryError(Exception):
    def __init__(self, message: str, gone: bool = False) -> None:
        super().__init__(message)
        self.gone = gone


def _slack_payload(n: dict[str, Any]) -> dict[str, Any]:
    url = service.absolute(n["link"])
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{n['title']}*"
                                                                 + (f"\n{n['body']}" if n["body"] else "")}}]
    if url:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open in Event Network Hub"}, "url": url}]})
    return {"text": n["title"], "blocks": blocks}  # text: the fallback for notifications


def _email_text(n: dict[str, Any]) -> str:
    url = service.absolute(n["link"])
    return "\n\n".join(x for x in (n["body"], url and f"Open: {url}",
                                   "This is an automatic message from Event Network Hub (Web Summit ICT).") if x)


def deliver_due(db_path, sender: Sender) -> int:
    """Send every due delivery once. Returns how many were sent."""
    conn = db.connect(db_path)
    sent = 0
    try:
        now = datetime.now(timezone.utc)
        due = conn.execute(
            "SELECT d.*, n.title, n.body, n.link, n.severity, n.id AS nid FROM notification_deliveries d "
            "JOIN notifications n ON n.id = d.notification_id WHERE d.status = 'pending' AND d.next_try_at <= ? "
            "ORDER BY d.id LIMIT 50", (now.isoformat(timespec="seconds"),)).fetchall()
        for d in due:
            n = dict(d)
            attempts = d["attempts"] + 1
            try:
                if d["channel"] == "push":
                    sub = conn.execute("SELECT * FROM push_subscriptions WHERE id = ?", (int(d["target"]),)).fetchone()
                    if sub is None:
                        raise DeliveryError("subscription removed", gone=True)
                    sender.send_push(dict(sub), {"title": n["title"], "body": n["body"] or "",
                                                 "url": n["link"] or "/", "tag": f"n{n['nid']}",
                                                 "urgent": n["severity"] == "urgent"}, n["severity"] == "urgent")
                elif d["channel"] == "slack":
                    sender.send_slack(_slack_payload(n))
                else:
                    sender.send_email(d["target"], n["title"], _email_text(n))
            except Exception as exc:  # noqa: BLE001 - every channel failure is retried the same way
                gone = isinstance(exc, DeliveryError) and exc.gone
                if gone and d["channel"] == "push":
                    with conn:
                        conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (int(d["target"]),))
                final = gone or attempts >= MAX_ATTEMPTS
                delay = min(15 * 2 ** (attempts - 1), 3600)
                with conn:
                    conn.execute(
                        "UPDATE notification_deliveries SET status = ?, attempts = ?, last_error = ?, next_try_at = ? "
                        "WHERE id = ?", ("failed" if final else "pending", attempts, str(exc)[:500],
                                         (now + timedelta(seconds=delay)).isoformat(timespec="seconds"), d["id"]))
                log.warning("%s delivery %s failed (attempt %s): %s", d["channel"], d["id"], attempts, exc)
                continue
            with conn:
                conn.execute("UPDATE notification_deliveries SET status = 'sent', attempts = ?, sent_at = ? WHERE id = ?",
                             (attempts, now.isoformat(timespec="seconds"), d["id"]))
            sent += 1
    finally:
        conn.close()
    return sent


# --- scheduled checks ----------------------------------------------------------------------------


def check_sla(db_path, now: datetime | None = None) -> int:
    """P1/P2 tickets not acknowledged: warn at 4 minutes, escalate when the 5-minute SLA is
    missed (Slack, push, and the escalation emails). Lower severities: in-app only on breach."""
    from ..tickets import service as tickets_service, store as tickets_store

    now = now or datetime.now(timezone.utc)
    conn = db.connect(db_path)
    created = 0
    try:
        with conn:
            for t in tickets_store.list_tickets(conn, statuses=("new",), limit=10_000):
                if t["acknowledged_at"] or t["merged_into"]:
                    continue
                age = (now - datetime.fromisoformat(t["created_at"])).total_seconds() / 60
                urgent = t["severity"] in ("P1", "P2")
                sla = tickets_service.sla(t, now)
                link = f"/helpdesk?view=tickets&ticket={t['id']}"
                if urgent and age >= SLA_WARN_MINUTES and sla["ack"] == "pending":
                    if service.notify(conn, type="sla_warning", severity="urgent", event_id=t["event_id"],
                                      title=f"#{t['id']} not acknowledged yet ({t['severity']})",
                                      body=f"{t['title']} — acknowledge within the next minute to meet the 5-minute SLA.",
                                      link=link, push_type="sla", slack=True, dedup_key=f"sla-warn:{t['id']}"):
                        created += 1
                if sla["ack"] == "breached":
                    if service.notify(conn, type="sla_breach", severity="urgent" if urgent else "normal",
                                      event_id=t["event_id"], title=f"SLA missed: #{t['id']} not acknowledged ({t['severity']})",
                                      body=f"{t['title']} — raised {int(age)} minutes ago and still unowned.",
                                      link=link, push_type="sla" if urgent else None, slack=urgent,
                                      emails=service.CHANNELS.escalation_emails if urgent else (),
                                      dedup_key=f"sla-breach:{t['id']}"):
                        created += 1
    finally:
        conn.close()
    return created


def check_deadlines(db_path, today: date | None = None) -> int:
    """Remind team contacts 7 days and 1 day before an event's ICT request deadline."""
    today = today or datetime.now(timezone.utc).date()
    conn = db.connect(db_path)
    created = 0
    try:
        with conn:
            rows = conn.execute(
                "SELECT l.id, l.team, l.token, l.contact_email, s.deadline, d.id AS doc, d.name FROM request_links l "
                "JOIN request_settings s ON s.document_id = l.document_id JOIN documents d ON d.id = l.document_id "
                "WHERE l.revoked_at IS NULL AND l.contact_email IS NOT NULL AND s.deadline IS NOT NULL "
                "AND d.archived_at IS NULL").fetchall()
            from ..ictrequests.models import REQUEST_TEAMS

            for r in rows:
                days = (date.fromisoformat(r["deadline"]) - today).days
                if days not in REMINDER_DAYS:
                    continue
                code = r["name"].split(" | ")[0]
                url = f"{service.CHANNELS.public_url}/request/{r['token']}"
                if service.notify(conn, type="request_deadline", severity="low", audience="external", event_id=r["doc"],
                                  title=f"Reminder: ICT requests for {code} are due in {days} day{'s' if days != 1 else ''}",
                                  body=f"Send your team's ({REQUEST_TEAMS.get(r['team'], r['team'])}) wired and Wi-Fi requests "
                                       f"by {r['deadline']}:\n{url}", link=url, emails=[r["contact_email"]],
                                  dedup_key=f"deadline:{r['id']}:{r['deadline']}:{days}"):
                    created += 1
    finally:
        conn.close()
    return created


async def run(settings, sender: Sender) -> None:
    """Background loop: deliveries every 5 s, SLA checks every 30 s, deadline checks hourly."""
    tick = 0
    while True:
        try:
            if tick % 6 == 0 and settings.helpdesk_enabled:
                await run_in_threadpool(check_sla, settings.db_path)
            if tick % 720 == 0:
                await run_in_threadpool(check_deadlines, settings.db_path)
            await run_in_threadpool(deliver_due, settings.db_path, sender)
        except Exception:
            log.exception("notification worker failed")
        tick += 1
        await asyncio.sleep(POLL_SECONDS)
