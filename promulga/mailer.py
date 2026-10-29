"""E-mail: everything goes through the outbox table.

`queue()` renders a template and writes it to the outbox inside the caller's
transaction, so an e-mail exists only if the action that caused it was saved.
`flush_outbox()` (run every minute by the scheduler) sends pending e-mails
over SMTP. Without SMTP settings nothing is sent, and the administrator can
read the e-mails in Admin > Outbox, which is handy for demos and tests.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy.orm import Session

from .config import get_config
from .models import OutboxMail, utcnow

log = logging.getLogger(__name__)

_env = Environment(loader=PackageLoader("promulga", "templates/email"),
                   autoescape=select_autoescape(["html"]))

MAX_ATTEMPTS = 5


def queue(db: Session, to: str, subject: str, template: str, site: dict, **context) -> OutboxMail:
    html = _env.get_template(template).render(site=site, base_url=get_config().base_url, **context)
    mail = OutboxMail(to=to, subject=f"[{site['site_name']}] {subject}", html=html)
    db.add(mail)
    return mail


def flush_outbox(db: Session, limit: int = 50) -> int:
    """Sends pending e-mails. Returns the number sent."""
    cfg = get_config()
    if not cfg.smtp_enabled:
        return 0
    pending = (db.query(OutboxMail)
               .filter(OutboxMail.sent_at.is_(None), OutboxMail.attempts < MAX_ATTEMPTS)
               .order_by(OutboxMail.id).limit(limit).all())
    if not pending:
        return 0
    sent = 0
    try:
        server = _connect(cfg)
    except Exception as exc:  # network problems: retry at the next run
        log.warning("SMTP connection failed: %s", exc)
        for mail in pending:
            mail.attempts += 1
            mail.error = f"connection: {exc}"[:500]
        return 0
    try:
        for mail in pending:
            msg = EmailMessage()
            msg["From"] = cfg.smtp_from if "<" in cfg.smtp_from else formataddr(("Promulga", cfg.smtp_from))
            msg["To"] = mail.to
            msg["Subject"] = mail.subject
            msg.set_content("This message needs an e-mail client that displays HTML.")
            msg.add_alternative(mail.html, subtype="html")
            mail.attempts += 1
            try:
                server.send_message(msg)
                mail.sent_at = utcnow()
                mail.error = ""
                sent += 1
            except Exception as exc:
                mail.error = str(exc)[:500]
                log.warning("Sending to %s failed: %s", mail.to, exc)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent


def _connect(cfg) -> smtplib.SMTP:
    if cfg.smtp_ssl:
        server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ssl.create_default_context(), timeout=30)
    else:
        server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
        if cfg.smtp_starttls:
            server.starttls(context=ssl.create_default_context())
    if cfg.smtp_user:
        server.login(cfg.smtp_user, cfg.smtp_password)
    return server
