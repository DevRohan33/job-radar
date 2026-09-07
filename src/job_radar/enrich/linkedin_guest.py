"""Stage 3 - Enrich: fetch the public job description for a small shortlist.

Alert emails carry title/company/location but never the description, and skills
matching on a title alone is shallow. This module fetches LinkedIn's public
guest endpoint for the top N jobs only, caches every result permanently by
job_id, and degrades to email-only metadata when a fetch fails (Trap 2) -
enrichment failing must never fail the run.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser

from ..config import JD_CACHE_DIR, Settings

log = logging.getLogger(__name__)

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# LinkedIn answers bots with 999; 429 is the ordinary rate limit.
BLOCKED_STATUSES = {403, 429, 999}
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class JobDetail:
    job_id: str
    description: str = ""
    seniority: str = ""
    employment_type: str = ""
    applicants: int | None = None
    fetched_at: str = ""
    ok: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EnrichmentReport:
    attempted: int = 0
    from_cache: int = 0
    fetched: int = 0
    failed: int = 0
    blocked: bool = False
    errors: list[str] = field(default_factory=list)


def _cache_path(job_id: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or JD_CACHE_DIR) / f"{job_id}.json"


def read_cache(job_id: str, cache_dir: Path | None = None) -> JobDetail | None:
    path = _cache_path(job_id, cache_dir)
    if not path.exists():
        return None
    try:
        return JobDetail(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        log.warning("enrich: unreadable cache entry %s", path)
        return None


def write_cache(detail: JobDetail, cache_dir: Path | None = None) -> None:
    path = _cache_path(detail.job_id, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(detail.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")


def parse_job_page(html: str, job_id: str) -> JobDetail:
    """Pull description + criteria out of the guest job-posting fragment."""
    tree = HTMLParser(html)
    detail = JobDetail(job_id=job_id, ok=True, fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    body = tree.css_first(".show-more-less-html__markup") or tree.css_first(".description__text")
    if body is None:
        body = tree.css_first("section") or tree.body
    if body is not None:
        text = body.text(separator="\n", strip=True)
        detail.description = re.sub(r"\n{3,}", "\n\n", text).strip()

    labels = tree.css(".description__job-criteria-subheader, .description__job-criteria-item h3")
    values = tree.css(".description__job-criteria-text, .description__job-criteria-item span")
    pairs = {
        " ".join(l.text().split()).lower(): " ".join(v.text().split())
        for l, v in zip(labels, values)
    }
    detail.seniority = pairs.get("seniority level", "")
    detail.employment_type = pairs.get("employment type", "")

    m = re.search(r"([\d,]+)\s+applicants", html, re.I)
    if m:
        try:
            detail.applicants = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    if not detail.description:
        detail.ok = False
        detail.error = "no description block found"
    return detail


def fetch_job_detail(
    job_id: str,
    client: httpx.Client,
    cache_dir: Path | None = None,
) -> JobDetail:
    url = GUEST_URL.format(job_id=job_id)
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = client.get(url, headers=headers)
    if response.status_code in BLOCKED_STATUSES:
        raise BlockedError(f"HTTP {response.status_code} for job {job_id}")
    response.raise_for_status()
    detail = parse_job_page(response.text, job_id)
    if detail.ok:
        write_cache(detail, cache_dir)
    return detail


class BlockedError(RuntimeError):
    """LinkedIn refused the request - stop fetching for this run."""


def enrich_jobs(
    jobs: list,
    settings: Settings,
    cache_dir: Path | None = None,
) -> EnrichmentReport:
    """Fill `job.jd_text` for the shortlist, in place. Never raises."""
    report = EnrichmentReport()
    if not settings.enrich_enabled or not jobs:
        return report

    pending: list = []
    for job in jobs:
        cached = read_cache(job.job_id, cache_dir)
        if cached and cached.ok:
            _apply(job, cached)
            report.from_cache += 1
        else:
            pending.append(job)

    if not pending:
        return report

    consecutive = 0
    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for i, job in enumerate(pending):
            report.attempted += 1
            try:
                detail = fetch_job_detail(job.job_id, client, cache_dir)
                if detail.ok:
                    _apply(job, detail)
                    report.fetched += 1
                    consecutive = 0
                else:
                    report.failed += 1
                    consecutive += 1
                    report.errors.append(f"{job.job_id}: {detail.error}")
            except BlockedError as exc:
                report.failed += 1
                consecutive += 1
                report.errors.append(str(exc))
                log.warning("enrich: %s", exc)
            except Exception as exc:  # network, parse, anything
                report.failed += 1
                consecutive += 1
                report.errors.append(f"{job.job_id}: {exc!r}")
                log.warning("enrich: job %s failed: %r", job.job_id, exc)

            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                report.blocked = True
                log.warning(
                    "enrich: %d consecutive failures - stopping enrichment, "
                    "falling back to email-only metadata for the rest",
                    consecutive,
                )
                break

            if i < len(pending) - 1:
                time.sleep(random.uniform(settings.enrich_min_delay, settings.enrich_max_delay))

    return report


SENIORITY_FROM_LINKEDIN = {
    "internship": "Intern",
    "entry level": "Junior",
    "associate": "Mid",
    "mid-senior level": "Senior",
    "director": "Staff+",
    "executive": "Staff+",
}

EMPLOYMENT_FROM_LINKEDIN = {
    "full-time": "Full-time",
    "part-time": "Part-time",
    "contract": "Contract",
    "temporary": "Contract",
    "internship": "Internship",
    "volunteer": "Full-time",
}


def _apply(job, detail: JobDetail) -> None:
    """Copy the fetched detail onto the Job. LinkedIn's own criteria beat inference."""
    job.jd_text = detail.description
    job.enriched = bool(detail.description)
    if detail.applicants and not job.applicants:
        job.applicants = detail.applicants
    mapped = SENIORITY_FROM_LINKEDIN.get(detail.seniority.strip().lower())
    if mapped:
        job.seniority = mapped
    mapped = EMPLOYMENT_FROM_LINKEDIN.get(detail.employment_type.strip().lower())
    if mapped:
        job.vacancy_type = mapped
