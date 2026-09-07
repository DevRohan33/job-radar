"""A local CSV mirror of the sheet.

Same columns, same dedup key. Lets you run the whole pipeline before the Google
credentials exist, and gives every run an offline artifact to inspect.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterable

from ..config import STATE_DIR
from ..models import SHEET_COLUMNS, Job, job_to_row

log = logging.getLogger(__name__)

DEFAULT_PATH = STATE_DIR / "jobs.csv"


def existing_job_ids(path: Path | None = None) -> set[str]:
    target = path or DEFAULT_PATH
    if not target.exists():
        return set()
    with target.open(newline="", encoding="utf-8") as fh:
        return {row.get("job_id", "").strip() for row in csv.DictReader(fh) if row.get("job_id")}


def append_jobs(jobs: Iterable[Job], path: Path | None = None) -> list[Job]:
    target = path or DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    known = existing_job_ids(target)
    fresh = [j for j in jobs if j.job_id not in known]
    fresh.sort(key=lambda j: j.match_score, reverse=True)
    if not fresh:
        log.info("csv: nothing new to append")
        return []
    write_header = not target.exists() or target.stat().st_size == 0
    with target.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(SHEET_COLUMNS)
        for job in fresh:
            writer.writerow(job_to_row(job))
    log.info("csv: appended %d new job(s) to %s", len(fresh), target)
    return fresh
