"""Email notices to reviewers: "pairs wait for you in project X" after a run, and "N pairs were given to you" after an assignment.

Off unless the server sets an SMTP server (environment variables only; nothing is configured in the portal and no password is ever stored):

  DEDUPE_SMTP_HOST       the mail server (notices are off when it is not set)
  DEDUPE_SMTP_PORT       default 587
  DEDUPE_SMTP_FROM       the sender address (required with the host)
  DEDUPE_SMTP_USER       optional sign-in name; the password is read from DEDUPE_SMTP_PASSWORD
  DEDUPE_SMTP_STARTTLS   default on: the connection is encrypted before signing in; set 0 only for a mail relay on the same machine
  DEDUPE_PORTAL_URL      the portal's address, for the link in the notice (for example https://dedupe.company.local)

A notice holds no personal data: the project's name, counts and a link. Each one is sent in the background, so a slow or broken mail server
never slows a run or a click; a failure is logged (never the addresses) and the in-portal Home page and Review inbox still show the work.
"""
import logging
import os
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger("dedupe.notify")


@dataclass(frozen=True)
class Notifier:
    host: str | None = None
    port: int = 587
    sender: str | None = None
    user: str | None = None
    starttls: bool = True
    portal_url: str = ""

    @classmethod
    def from_env(cls) -> "Notifier":
        host = os.environ.get("DEDUPE_SMTP_HOST", "").strip() or None
        try:
            port = int(os.environ.get("DEDUPE_SMTP_PORT", "").strip() or 587)
        except ValueError:
            log.error("DEDUPE_SMTP_PORT is not a number; email notices are off")
            return cls()
        return cls(host=host, port=port, sender=os.environ.get("DEDUPE_SMTP_FROM", "").strip() or None,
                   user=os.environ.get("DEDUPE_SMTP_USER", "").strip() or None,
                   starttls=os.environ.get("DEDUPE_SMTP_STARTTLS", "1").strip().lower() not in ("0", "false", "no", "off"),
                   portal_url=os.environ.get("DEDUPE_PORTAL_URL", "").strip().rstrip("/"))

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.sender)

    def link(self, path: str) -> str:
        return (self.portal_url + path) if self.portal_url else "the portal, " + path

    def send(self, to: list[str], subject: str, body: str, wait: bool = False) -> threading.Thread | None:
        """Send one notice to each address (each gets its own message: no one sees who else was told)."""
        to = sorted({a for a in to if a})
        if not self.enabled or not to:
            return None
        t = threading.Thread(target=self._deliver, args=(to, subject, body), daemon=True, name="notify")
        t.start()
        if wait:
            t.join(30)
        return t

    def _deliver(self, to: list[str], subject: str, body: str) -> None:
        try:
            with smtplib.SMTP(self.host, self.port, timeout=15) as s:
                if self.starttls:
                    s.starttls(context=ssl.create_default_context())
                if self.user:
                    s.login(self.user, os.environ.get("DEDUPE_SMTP_PASSWORD", ""))
                for addr in to:
                    m = EmailMessage()
                    m["From"], m["To"], m["Subject"] = self.sender, addr, subject
                    m.set_content(body)
                    s.send_message(m)
        except (OSError, smtplib.SMTPException) as e:
            log.error("email notice not sent (%s) to %d recipient(s)", type(e).__name__, len(to))


__all__ = ["Notifier"]
