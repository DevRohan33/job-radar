"""Stage 1 - Collect: pull LinkedIn alert emails from Gmail over IMAP.

IMAP + App Password on purpose: OAuth refresh tokens issued by a consent screen
left in Testing status expire after 7 days and the automation dies silently.
See Trap 1 in docs/DESIGN.md.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Iterator, Sequence

from ..config import Settings

log = logging.getLogger(__name__)

LINKEDIN_SENDERS = ("linkedin.com",)
_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class RawEmail:
    """One alert email, decoded far enough for the parser to work on."""

    message_id: str
    subject: str
    received: date
    html: str
    text: str

    @property
    def alert_name(self) -> str:
        """The saved search that produced this email, for the `source` column."""
        m = re.search(r"[\"'“‘]([^\"'”’]{2,60})[\"'”’]", self.subject)
        if m:
            return m.group(1).strip()
        cleaned = re.sub(r"^\d+\s+new jobs?\s+(for|in)\s+", "", self.subject, flags=re.I)
        return cleaned.strip()[:60] or "LinkedIn alert"


def _imap_date(d: date) -> str:
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_parts(msg: Message) -> tuple[str, str]:
    """Return (html, text) with a best-effort charset decode."""
    html_chunks: list[str] = []
    text_chunks: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            body = payload.decode(charset, errors="replace")
        except LookupError:
            body = payload.decode("utf-8", errors="replace")
        (html_chunks if ctype == "text/html" else text_chunks).append(body)
    return "\n".join(html_chunks), "\n".join(text_chunks)


def email_from_bytes(raw: bytes, fallback_id: str = "") -> RawEmail:
    msg = email.message_from_bytes(raw)
    html, text = _body_parts(msg)
    received = date.today()
    if msg.get("Date"):
        try:
            received = email.utils.parsedate_to_datetime(msg["Date"]).date()
        except Exception:
            pass
    return RawEmail(
        message_id=_decode(msg.get("Message-ID")) or fallback_id,
        subject=_decode(msg.get("Subject")),
        received=received,
        html=html,
        text=text,
    )


def fetch_raw(settings: Settings, since_days: int | None = None, limit: int | None = None):
    """Every LinkedIn message since the cutoff, as (uid, raw bytes).

    The single place that talks IMAP. Never marks mail as read - BODY.PEEK
    leaves your inbox exactly as you left it.
    """
    settings.require_gmail()
    days = since_days if since_days is not None else settings.lookback_days
    since = date.today() - timedelta(days=days)
    out: list[tuple[str, bytes]] = []

    conn = imaplib.IMAP4_SSL(settings.imap_host)
    try:
        conn.login(settings.gmail_user, settings.gmail_app_password)
        status, _ = conn.select(settings.imap_folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not select IMAP folder {settings.imap_folder!r}")

        uids: list[bytes] = []
        for sender in LINKEDIN_SENDERS:
            status, data = conn.search(None, "SINCE", _imap_date(since), "FROM", f'"{sender}"')
            if status == "OK" and data and data[0]:
                uids.extend(data[0].split())

        log.info("IMAP: %d LinkedIn message(s) since %s", len(uids), since.isoformat())
        for uid in uids[:limit] if limit else uids:
            status, data = conn.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                log.warning("IMAP: could not fetch uid %s", uid)
                continue
            out.append((uid.decode(), data[0][1]))
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()
    return out


def fetch_emails(settings: Settings, since_days: int | None = None) -> list[RawEmail]:
    """Stage 1."""
    return [email_from_bytes(raw, fallback_id=uid) for uid, raw in fetch_raw(settings, since_days)]


def save_fixtures(settings: Settings, out_dir: Path, days: int = 7, limit: int = 10) -> list[Path]:
    """Write real alert emails to disk as .eml - the parser's regression net."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for uid, raw in fetch_raw(settings, days, limit):
        msg = email.message_from_bytes(raw)
        stamp = (msg.get("Date") or "").replace(":", "-")[:20].strip().replace(" ", "_")
        path = out_dir / f"alert_{stamp or uid}.eml"
        path.write_bytes(raw)
        saved.append(path)
    return saved


def load_fixtures(directory: str | Path) -> list[RawEmail]:
    """Offline source: every .eml under `directory`. Used by tests and --fixtures."""
    path = Path(directory)
    files: Sequence[Path] = sorted(path.glob("*.eml")) if path.is_dir() else [path]
    return [email_from_bytes(f.read_bytes(), fallback_id=f.name) for f in files]


def iter_sources(settings: Settings, fixtures: str | None) -> Iterator[RawEmail]:
    yield from (load_fixtures(fixtures) if fixtures else fetch_emails(settings))
