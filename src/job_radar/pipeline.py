"""The five stages, wired together.

Order matters for cost: dedup against the sheet happens *before* enrichment and
AI scoring, so a job you already have never costs a fetch or a token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .config import PROFILE_DIR, STATE_DIR, Settings
from .profile import Filters, SkillsProfile, load_cv, load_filters, load_skills
from .enrich.linkedin_guest import EnrichmentReport, enrich_jobs
from .models import Job
from .score import llm as llm_scorer
from .score.rules import check_gates, score_job
from .sinks import csv_sink
from .sources.gmail_linkedin import iter_sources
from .sources.parse import parse_email, resolve_posted_at

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    fixtures: str | None = None
    dry_run: bool = False
    csv_only: bool = False
    limit: int | None = None
    skip_enrich: bool = False
    skip_llm: bool = False


@dataclass
class RunReport:
    emails: int = 0
    cards: int = 0
    unique: int = 0
    already_known: int = 0
    gated_out: int = 0
    scored: int = 0
    published: int = 0
    enrichment: EnrichmentReport = field(default_factory=EnrichmentReport)
    llm_rescored: int = 0
    gate_reasons: dict[str, int] = field(default_factory=dict)
    top: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def zero_yield(self) -> bool:
        """Parsed nothing from a non-empty inbox: the parser has probably broken."""
        return self.emails > 0 and self.cards == 0

    def summary(self) -> str:
        lines = [
            f"emails={self.emails} cards={self.cards} unique={self.unique} "
            f"known={self.already_known} gated_out={self.gated_out} "
            f"scored={self.scored} published={self.published}",
            f"enrich: cache={self.enrichment.from_cache} fetched={self.enrichment.fetched} "
            f"failed={self.enrichment.failed} blocked={self.enrichment.blocked}",
            f"llm: rescored={self.llm_rescored}",
        ]
        if self.gate_reasons:
            top_reasons = sorted(self.gate_reasons.items(), key=lambda kv: -kv[1])[:5]
            lines.append("gates: " + "; ".join(f"{r} x{n}" for r, n in top_reasons))
        for score, title, company in self.top[:5]:
            lines.append(f"  {score:>3}  {title} - {company}")
        return "\n".join(lines)


def collect(settings: Settings, options: RunOptions, report: RunReport) -> list[Job]:
    """Stages 1 + 2: inbox (or fixtures) -> unique Job records."""
    jobs: dict[str, Job] = {}
    for email in iter_sources(settings, options.fixtures):
        report.emails += 1
        cards = parse_email(email)
        report.cards += len(cards)
        log.info("parsed %2d job(s) from %r", len(cards), email.subject[:70])
        for card in cards:
            if card.job_id in jobs:
                continue
            job = Job.from_card(card)
            job.posted_at = resolve_posted_at(card.posted_hint, email.received)
            job.first_seen = date.today()
            jobs[card.job_id] = job
    report.unique = len(jobs)
    return list(jobs.values())


def _known_ids(settings: Settings, options: RunOptions) -> tuple[set[str], Any]:
    """Existing job ids, plus an open worksheet handle when we have one."""
    if options.csv_only or options.dry_run:
        return csv_sink.existing_job_ids(), None
    from .sinks.sheets import existing_job_ids, open_worksheet

    worksheet = open_worksheet(settings)
    return existing_job_ids(worksheet), worksheet


def run(settings: Settings, options: RunOptions | None = None) -> RunReport:
    options = options or RunOptions()
    report = RunReport()

    skills: SkillsProfile = load_skills()
    filters: Filters = load_filters()
    cv_text = load_cv()
    if not cv_text.strip():
        # Degrades rather than fails - but silently, and only the log will ever
        # say so. Without a CV the similarity term of adjacent_skills scores a
        # flat zero and the LLM judges every job against an empty <cv> block.
        log.warning(
            "no CV loaded from %s - scoring without CV<->JD similarity, "
            "and the AI layer has nothing to compare against",
            PROFILE_DIR / "cv.md",
        )
    _apply_profile_budget(settings, filters)

    jobs = collect(settings, options, report)
    if not jobs:
        log.warning("no job cards parsed from %d email(s)", report.emails)
        return report

    known, worksheet = _known_ids(settings, options)
    jobs = [j for j in jobs if j.job_id not in known]
    report.already_known = report.unique - len(jobs)
    log.info("%d new job(s) after dedup against %d known id(s)", len(jobs), len(known))

    # Gate on what the email alone tells us, then score, so the enrichment
    # shortlist is chosen by rule score rather than arrival order.
    kept: list[Job] = []
    for job in jobs:
        gate = check_gates(job, filters)
        if not gate.passed:
            report.gated_out += 1
            report.gate_reasons[gate.reason] = report.gate_reasons.get(gate.reason, 0) + 1
            continue
        score_job(job, skills, filters, cv_text)
        kept.append(job)

    kept.sort(key=lambda j: j.rule_score, reverse=True)
    if options.limit:
        kept = kept[: options.limit]

    # Stage 3: enrich the top slice only, and only jobs the rules already like.
    if settings.enrich_enabled and not options.skip_enrich:
        worth_fetching = [j for j in kept if j.rule_score >= filters.skip_if_rule_score_below]
        shortlist = worth_fetching[: settings.enrich_top_n]
        report.enrichment = enrich_jobs(shortlist, settings)
        rescored: list[Job] = []
        for job in kept:
            if job.enriched:
                gate = check_gates(job, filters)
                if not gate.passed:
                    report.gated_out += 1
                    reason = f"{gate.reason} (after JD fetch)"
                    report.gate_reasons[reason] = report.gate_reasons.get(reason, 0) + 1
                    continue
                job.why_matched = ""
                job.jd_summary = ""
                score_job(job, skills, filters, cv_text)
            rescored.append(job)
        kept = rescored
        kept.sort(key=lambda j: j.rule_score, reverse=True)

    # Stage 4b: the optional AI layer, over the rule-ranked shortlist.
    if settings.llm_enabled and not options.skip_llm:
        report.llm_rescored = llm_scorer.score_jobs(kept, settings, cv_text, skills, filters)
        kept.sort(key=lambda j: j.match_score, reverse=True)

    report.scored = len(kept)
    report.top = [(j.match_score, j.role_title, j.company) for j in kept[:10]]

    # Stage 5: publish.
    published: list[Job] = []
    if options.dry_run:
        log.info("dry run - nothing written")
    elif options.csv_only:
        published = csv_sink.append_jobs(kept)
    else:
        from .sinks.sheets import append_jobs

        published = append_jobs(worksheet, kept)
        csv_sink.append_jobs(kept)  # local mirror, same dedup key
    report.published = len(published)

    if published:
        from .notify.digest import send_digest

        send_digest(published, settings)

    _write_heartbeat(report)
    return report


def _apply_profile_budget(settings: Settings, filters: Filters) -> None:
    """filters.yaml owns the enrichment budget; env vars only override it."""
    import os

    if "ENRICH_TOP_N" not in os.environ:
        settings.enrich_top_n = filters.max_jd_fetches_per_run
    if "ENRICH_MIN_DELAY" not in os.environ:
        settings.enrich_min_delay = filters.min_delay_seconds
    if "ENRICH_MAX_DELAY" not in os.environ:
        settings.enrich_max_delay = filters.max_delay_seconds


def _write_heartbeat(report: RunReport) -> None:
    """A one-line state file per run.

    Committed by the workflow so the repository never looks idle - GitHub
    disables cron workflows after 60 days without activity (Trap 3).
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path: Path = STATE_DIR / "last_run.txt"
    path.write_text(
        f"{date.today().isoformat()} emails={report.emails} cards={report.cards} "
        f"published={report.published} scored={report.scored}\n",
        encoding="utf-8",
    )
