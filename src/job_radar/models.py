"""The Job record every stage speaks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

WorkMode = Literal["Remote", "Hybrid", "On-site", "Unknown"]
VacancyType = Literal["Full-time", "Contract", "Internship", "Part-time", "Unknown"]
Seniority = Literal["Intern", "Junior", "Mid", "Senior", "Staff+", "Unknown"]
Verdict = Literal["Apply now", "Strong", "Worth a look", "Archive"]


class RawCard(BaseModel):
    """What Stage 2 can see in an alert email, before any enrichment."""

    job_id: str
    role_title: str
    company: str = ""
    location: str = ""
    apply_url: str = ""
    source: str = ""
    posted_hint: str = ""
    applicants_hint: Optional[int] = None
    salary_hint: str = ""

    @field_validator("role_title", "company", "location", "salary_hint", mode="before")
    @classmethod
    def _clean(cls, v: object) -> str:
        return " ".join(str(v or "").split())


class Job(BaseModel):
    """The record that lands in the sheet."""

    job_id: str
    first_seen: date = Field(default_factory=date.today)
    match_score: int = 0
    verdict: Verdict = "Archive"
    company: str = ""
    role_title: str = ""
    seniority: Seniority = "Unknown"
    vacancy_type: VacancyType = "Unknown"
    work_mode: WorkMode = "Unknown"
    location: str = ""
    posted_at: Optional[date] = None
    applicants: Optional[int] = None
    salary: str = ""
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    why_matched: str = ""
    jd_summary: str = ""
    apply_url: str = ""
    source: str = ""

    # Not written to the sheet - working state only.
    jd_text: str = Field(default="", exclude=True)
    enriched: bool = Field(default=False, exclude=True)
    rule_score: int = Field(default=0, exclude=True)
    score_breakdown: dict[str, float] = Field(default_factory=dict, exclude=True)

    @classmethod
    def from_card(cls, card: RawCard) -> "Job":
        return cls(
            job_id=card.job_id,
            company=card.company,
            role_title=card.role_title,
            location=card.location,
            apply_url=card.apply_url,
            source=card.source,
            applicants=card.applicants_hint,
            salary=card.salary_hint,
        )


# Sheet column order. The script owns these; `status` and `notes` belong to you
# and are appended blank, then never touched again.
SHEET_COLUMNS: list[str] = [
    "job_id",
    "first_seen",
    "match_score",
    "verdict",
    "company",
    "role_title",
    "seniority",
    "vacancy_type",
    "work_mode",
    "location",
    "posted_at",
    "applicants",
    "salary",
    "matched_skills",
    "missing_skills",
    "why_matched",
    "jd_summary",
    "apply_url",
    "source",
    "status",
    "notes",
]


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value)


def job_to_row(job: Job) -> list[str]:
    """Serialize a Job into SHEET_COLUMNS order, with status seeded to 'New'."""
    data = job.model_dump()
    row: list[str] = []
    for col in SHEET_COLUMNS:
        if col == "status":
            row.append("New")
        elif col == "notes":
            row.append("")
        else:
            row.append(_fmt(data.get(col)))
    return row
