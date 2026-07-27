"""Email digests over Gmail SMTP.

Sends through the same dedicated mailbox and the same app password already
needed to *read* her job alerts over IMAP. That deliberately removes an
account and a key from the setup rather than adding them, and the digest
arrives from an address she owns, so it doesn't land in spam the way a
third-party sender does.

Named `mail.py`, not `email.py`, because `email` is a standard-library
package and shadowing it breaks smtplib in confusing ways.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT = 30


def _score_colour(score: int) -> str:
    if score >= 9:
        return "#059669"
    if score >= 7:
        return "#2563EB"
    return "#64748B"


def _render_job(job) -> str:
    salary = ""
    if job.best.salary_min or job.best.salary_max:
        low, high = job.best.salary_min, job.best.salary_max
        if low and high:
            salary = f"£{int(low):,} – £{int(high):,}"
        else:
            salary = f"£{int(low or high):,}"

    where = ", ".join(job.locations) if job.locations else ""
    # Showing which sources carried a role is genuinely useful signal: one
    # advert on three boards is a more serious vacancy than one on a single
    # aggregator.
    sources = " · ".join(job.sources)
    meta = " · ".join(part for part in (where, salary, sources) if part)

    return f"""
      <tr><td style="padding:14px 0;border-bottom:1px solid #E2E8F0;">
        <div style="font-size:13px;font-weight:700;color:{_score_colour(job.score)};">
          {job.score}/10
        </div>
        <div style="font-size:17px;font-weight:600;margin:2px 0 4px;">
          <a href="{html.escape(job.url)}" style="color:#0F172A;text-decoration:none;">
            {html.escape(job.title)}
          </a>
        </div>
        <div style="font-size:15px;color:#334155;">{html.escape(job.company)}</div>
        <div style="font-size:13px;color:#64748B;margin-top:3px;">{html.escape(meta)}</div>
        <div style="font-size:14px;color:#475569;margin-top:6px;font-style:italic;">
          {html.escape(job.reason)}
        </div>
      </td></tr>"""


def render(jobs, *, feedback_url: str = "") -> str:
    rows = "".join(_render_job(job) for job in jobs)

    steer = ""
    if feedback_url:
        steer = f"""
        <p style="font-size:14px;color:#475569;margin-top:22px;">
          Not right? <a href="{html.escape(feedback_url)}" style="color:#2563EB;">
          Add a line to feedback.md</a> — one sentence is enough, and the next
          run picks it up.
        </p>"""

    return f"""<html><body style="margin:0;padding:22px;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
      background:#F8FAFC;color:#0F172A;">
      <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:14px;padding:26px;">
        <h1 style="font-size:22px;margin:0 0 4px;">Jobs worth a look</h1>
        <p style="font-size:14px;color:#64748B;margin:0 0 8px;">
          {len(jobs)} new role{'s' if len(jobs) != 1 else ''}, best first.
        </p>
        <table style="width:100%;border-collapse:collapse;">{rows}</table>
        {steer}
      </div></body></html>"""


def render_empty() -> str:
    return """<html><body style="margin:0;padding:22px;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
      background:#F8FAFC;color:#0F172A;">
      <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:14px;padding:26px;">
        <h1 style="font-size:20px;margin:0 0 6px;">Nothing new today</h1>
        <p style="font-size:15px;color:#475569;margin:0;">
          The agent ran fine and found nothing worth showing you. This note
          only exists so silence never has to mean "is it broken?".
        </p>
      </div></body></html>"""


def send(
    *, address: str, app_password: str, to: str, subject: str, body_html: str
) -> bool:
    """Send one email. Returns True on success; never raises.

    A failed digest must not abort a run that has already pushed, or one that
    still has state to write.
    """
    if not address or not app_password:
        log.warning("[email] no mailbox credentials; skipping")
        return False
    if not to:
        log.warning("[email] no destination address; skipping")
        return False

    message = EmailMessage()
    message["From"] = address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(
        "This digest is formatted as HTML. If you're seeing this, your mail "
        "app has HTML turned off."
    )
    message.add_alternative(body_html, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=TIMEOUT, context=context
        ) as smtp:
            smtp.login(address, app_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        log.error("[email] send failed: %s", exc)
        return False

    log.info("[email] sent '%s'", subject)
    return True


def send_failure_notice(*, address: str, app_password: str, to: str, reason: str) -> bool:
    """Tell her the run broke.

    Silence must only ever mean "nothing matched", never "it has been dead for
    a fortnight" -- which is exactly what a silent failure looks like from her
    side.
    """
    body = f"""<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;padding:20px;">
      <h2 style="font-size:19px;">The job agent hit a problem</h2>
      <p style="font-size:15px;color:#334155;">
        It didn't finish this run. Nothing is broken permanently and no
        settings have changed — it'll try again at the next scheduled time.
      </p>
      <pre style="background:#F1F5F9;padding:12px;border-radius:8px;
        font-size:13px;white-space:pre-wrap;">{html.escape(reason)}</pre>
      <p style="font-size:14px;color:#475569;">
        If it happens repeatedly, open a Claude session and paste this in,
        along with what the repo's Actions tab shows.
      </p></body></html>"""
    return send(
        address=address,
        app_password=app_password,
        to=to,
        subject="⚠️ Job agent — a run failed",
        body_html=body,
    )
