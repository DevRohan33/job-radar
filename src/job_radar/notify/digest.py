"""Phase 4 - the morning digest.

Five lines on your phone beats a spreadsheet you have to remember to open.
Silent no-op unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
"""

from __future__ import annotations

import logging

import httpx

from ..config import Settings
from ..models import Job

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(jobs: list[Job], min_score: int, top_n: int = 5) -> str:
    picks = [j for j in jobs if j.match_score >= min_score][:top_n]
    if not picks:
        return ""
    lines = [f"<b>Job Radar</b> - {len(picks)} worth your hour today"]
    for job in picks:
        title = _escape(job.role_title or "Untitled")
        company = _escape(job.company or "unknown company")
        lines.append(
            f"\n<b>{job.match_score}</b> · {title} - {company}\n"
            f"<i>{_escape(job.why_matched[:180])}</i>\n"
            f'<a href="{job.apply_url}">open posting</a>'
        )
    return "\n".join(lines)


def send_digest(jobs: list[Job], settings: Settings) -> bool:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    message = build_message(jobs, settings.digest_min_score)
    if not message:
        log.info("digest: nothing scored above %d today", settings.digest_min_score)
        return False
    try:
        response = httpx.post(
            API.format(token=settings.telegram_bot_token),
            json={
                "chat_id": settings.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        log.info("digest: sent %d pick(s) to Telegram", message.count("\n\n") + 1)
        return True
    except Exception as exc:  # a failed notification must not fail the run
        log.warning("digest: could not send: %r", exc)
        return False
