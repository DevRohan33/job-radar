"""Stage 2 - Parse: turn one alert email into RawCard records.

This is the only component that can break without warning, because LinkedIn owns
the HTML. Every extraction below has a fallback, and `parse_email` never raises -
a zero-yield run is reported by the pipeline's alarm instead (Trap 5).
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import date, timedelta

from selectolax.parser import HTMLParser, Node

from ..models import RawCard
from ..textutil import clean_apply_url, extract_job_id, normalize

log = logging.getLogger(__name__)

JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/", re.I)

NOISE_LINES = re.compile(
    r"^(easy apply|promoted|actively recruiting|be an early applicant|view job|view jobs|"
    r"see all jobs|see all|apply|apply now|unsubscribe|manage alerts?|new job|"
    r"your profile matches|this job|linkedin|sent to|hiring)\b",
    re.I,
)

APPLICANTS = re.compile(r"([\d,]+)\s*\+?\s*(?:applicants?|people clicked apply)", re.I)
SALARY = re.compile(
    r"(?:₹|rs\.?|inr|\$|usd|€|£)\s?[\d.,]+\s?(?:k|l|lpa|lakh|lakhs|cr|m|mn)?\s*"
    r"(?:-|–|to)\s*(?:₹|rs\.?|inr|\$|usd|€|£)?\s?[\d.,]+\s?"
    r"(?:k|l|lpa|lakh|lakhs|cr|m|mn)?"
    r"(?:\s*(?:/|per\s)?\s*(?:yr|year|annum|month|mo|hr|hour))?"
    r"|[\d.,]+\s*-\s*[\d.,]+\s*lpa",
    re.I,
)
AGE = re.compile(r"\b(\d+)\s*(minute|hour|day|week|month)s?\s+ago\b", re.I)
WORK_MODE_TAIL = re.compile(r"\((remote|hybrid|on-?site)\)\s*$", re.I)
SEPARATORS = re.compile(r"\s+[·•|]\s+|\s+-\s+")

ZERO_WIDTH = ("‌", "​", "﻿", "\xa0")


def _text(node: Node) -> str:
    try:
        return node.text(separator="\n", strip=True)
    except TypeError:  # older selectolax signature
        return node.text()


def _lines(block: str) -> list[str]:
    out: list[str] = []
    for raw in block.replace("\r", "").split("\n"):
        cleaned = html_lib.unescape(raw)
        for ch in ZERO_WIDTH:
            cleaned = cleaned.replace(ch, " ")
        line = " ".join(cleaned.split())
        if line and line not in out:
            out.append(line)
    return out


def _card_block(anchor: Node, title: str) -> str:
    """Climb to the smallest ancestor that holds more than just the job title."""
    node, best = anchor, anchor
    for _ in range(6):
        parent = node.parent
        if parent is None or parent.tag in ("body", "html"):
            break
        node = parent
        best = node
        if len(_text(node)) > len(title) + 15:
            break
    return _text(best)


def _looks_like_location(line: str) -> bool:
    if WORK_MODE_TAIL.search(line):
        return True
    if "," in line and len(line) < 90 and not APPLICANTS.search(line):
        # A location is a few short comma-separated parts: "Bengaluru, Karnataka, India".
        parts = [p.strip() for p in line.split(",")]
        if 2 <= len(parts) <= 4 and all(1 < len(p) <= 30 for p in parts):
            return True
    return bool(re.search(r"\b(remote|hybrid|on-?site|india|worldwide|anywhere)\b", line, re.I))


def _split_meta(lines: list[str], title: str) -> tuple[str, str]:
    """Pick (company, location) out of a card's remaining text lines."""
    company = location = ""
    norm_title = normalize(title)
    for line in lines:
        if normalize(line) == norm_title or NOISE_LINES.match(line) or AGE.search(line):
            continue
        if APPLICANTS.search(line):
            continue
        if SALARY.fullmatch(line.strip()):
            continue
        parts = [p.strip() for p in SEPARATORS.split(line) if p.strip()]
        if len(parts) >= 2:
            # "Acme Corp · Bengaluru, India (Hybrid)" - one line, both fields.
            if not company:
                company = parts[0]
            if not location and _looks_like_location(parts[-1]):
                location = parts[-1]
            continue
        if not location and _looks_like_location(line):
            location = line
            continue
        if not company:
            company = line
    return company, location


def _posted_hint(lines: list[str]) -> str:
    for line in lines:
        m = AGE.search(line)
        if m:
            return m.group(0)
        if re.search(r"\b(just now|today|yesterday)\b", line, re.I):
            return line.strip()[:40]
    return ""


def _applicants(lines: list[str]) -> int | None:
    for line in lines:
        m = APPLICANTS.search(line)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def _salary(lines: list[str]) -> str:
    for line in lines:
        if NOISE_LINES.match(line):
            continue
        m = SALARY.search(line)
        if m:
            return m.group(0).strip()
    return ""


def parse_html(html: str, source: str) -> list[RawCard]:
    """Group every anchor by job id first, then read the meta once per job.

    A card is usually linked several times over - logo, title, "view job" - and
    only one of those anchors carries the title. Extracting company and location
    per anchor would let a titleless one claim the title line as the company, so
    the anchors are merged before any of that is decided.
    """
    if not html.strip():
        return []
    tree = HTMLParser(html)
    unresolved = 0
    grouped: dict[str, dict] = {}

    for anchor in tree.css("a"):
        href = anchor.attributes.get("href") or ""
        if not JOB_LINK.search(href):
            continue
        job_id = extract_job_id(href)
        if not job_id:
            unresolved += 1
            continue
        title = " ".join(_text(anchor).split())
        if NOISE_LINES.match(title) or len(title) > 160:
            title = ""  # a logo or "View job" link
        entry = grouped.setdefault(job_id, {"title": "", "lines": [], "url": ""})
        if title and not entry["title"]:
            entry["title"] = title
        if not entry["url"]:
            entry["url"] = clean_apply_url(href, job_id)
        for line in _lines(_card_block(anchor, title)):
            if line not in entry["lines"]:
                entry["lines"].append(line)

    cards: list[RawCard] = []
    for job_id, entry in grouped.items():
        title, lines = entry["title"], entry["lines"]
        if not title:
            continue
        company, location = _split_meta(lines, title)
        cards.append(
            RawCard(
                job_id=job_id,
                role_title=title,
                company=company,
                location=location,
                apply_url=entry["url"],
                source=source,
                posted_hint=_posted_hint(lines),
                applicants_hint=_applicants(lines),
                salary_hint=_salary(lines),
            )
        )

    if unresolved:
        log.warning("parse: %d job link(s) had no extractable job id", unresolved)
    return cards


def parse_text(text: str, source: str) -> list[RawCard]:
    """Fallback for emails whose HTML part is missing or unparseable."""
    cards: dict[str, RawCard] = {}
    lines = _lines(text)
    for i, line in enumerate(lines):
        for url in re.findall(r"https?://[^\s>)\]]+", line):
            if not JOB_LINK.search(url):
                continue
            job_id = extract_job_id(url)
            if not job_id or job_id in cards:
                continue
            context = [x for x in lines[max(0, i - 4) : i] if x and not NOISE_LINES.match(x)]
            title = context[0] if context else ""
            company, location = _split_meta(context[1:], title)
            cards[job_id] = RawCard(
                job_id=job_id,
                role_title=title,
                company=company,
                location=location,
                apply_url=clean_apply_url(url, job_id),
                source=source,
                posted_hint=_posted_hint(context),
                applicants_hint=_applicants(context),
                salary_hint=_salary(context),
            )
    return [c for c in cards.values() if c.role_title]


def parse_email(raw_email, source: str | None = None) -> list[RawCard]:
    """Parse one RawEmail. Returns [] rather than raising - the alarm catches it."""
    label = source or getattr(raw_email, "alert_name", "") or "LinkedIn alert"
    try:
        cards = parse_html(getattr(raw_email, "html", ""), label)
    except Exception:
        log.exception("parse: HTML parser failed on %r", getattr(raw_email, "subject", ""))
        cards = []
    if not cards:
        try:
            cards = parse_text(getattr(raw_email, "text", ""), label)
        except Exception:
            log.exception("parse: text fallback failed")
            cards = []
    return cards


def resolve_posted_at(hint: str, received: date | None = None) -> date | None:
    """'3 days ago' plus the email's own date -> an absolute posting date."""
    base = received or date.today()
    if not hint:
        return None
    low = hint.lower()
    if "just now" in low or "today" in low:
        return base
    if "yesterday" in low:
        return base - timedelta(days=1)
    m = AGE.search(hint)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}[unit] * n
    return base - timedelta(days=days)
