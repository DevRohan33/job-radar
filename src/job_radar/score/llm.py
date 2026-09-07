"""Stage 4 - Score (optional AI layer), on the OpenAI API.

The rule scorer prefilters; only the day's top slice reaches the model, so the
bill stays small. Every failure path falls back to the rule score - the LLM is
an upgrade, never a dependency.

Swapping providers means rewriting `score_one` and nothing else. Any
OpenAI-compatible endpoint (Groq, Together, a local server) works by setting
LLM_BASE_URL, with no code change at all.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import STATE_DIR, Settings
from ..models import Job
from ..profile import Filters, SkillsProfile, display
from .rules import verdict_for

log = logging.getLogger(__name__)

LLM_CACHE_DIR = STATE_DIR / "llm_cache"

SYSTEM_PROMPT = """You are a hiring-match analyst for one specific job seeker.

You compare a job description against that person's CV and skills profile, and \
return a calibrated fit score.

Rules:
- Judge substance, not keywords. If the CV describes distributed systems work \
without using the phrase, that counts as a match; say so in the reason.
- `missing_skills` means skills the JD requires that the CV does not evidence. \
Nice-to-haves the person lacks are not missing skills.
- Be strict about seniority and years of experience. A role demanding markedly \
more experience than the CV shows cannot score above 55.
- `why_matched` is one sentence a busy person reads to decide whether to open \
the posting. No preamble, no restating the job title.
- Score 85+ only when you would tell this person to drop what they are doing \
and apply today.

Reply with JSON only."""

# Note for editors: OpenAI's strict structured outputs reject validation
# keywords like minimum/maximum/maxItems, so the bounds live in apply_result().
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "Fit score from 0 to 100"},
        "matched_skills": {"type": "array", "items": {"type": "string"}},
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "why_matched": {"type": "string"},
        "jd_summary": {"type": "string", "description": "About 40 words"},
        "seniority": {
            "type": "string",
            "enum": ["Intern", "Junior", "Mid", "Senior", "Staff+", "Unknown"],
        },
    },
    "required": [
        "score",
        "matched_skills",
        "missing_skills",
        "why_matched",
        "jd_summary",
        "seniority",
    ],
    "additionalProperties": False,
}

JSON_SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "job_match", "strict": True, "schema": RESPONSE_SCHEMA},
}
JSON_OBJECT_FORMAT = {"type": "json_object"}


def _cache_path(job_id: str) -> Path:
    return LLM_CACHE_DIR / f"{job_id}.json"


def _read_cache(job_id: str) -> dict[str, Any] | None:
    path = _cache_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(job_id: str, payload: dict[str, Any]) -> None:
    LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def build_prompt(job: Job, cv_text: str, skills: SkillsProfile) -> str:
    def tier(name: str) -> str:
        listed = skills.tier(name).skills
        return ", ".join(display(s) for s in listed) or "(none listed)"

    jd = job.jd_text or "(no description available - judge from the title and company only)"
    return f"""<candidate_profile>
Total professional experience: {skills.years_experience} years
Core skills, production-evidenced: {tier('core')}
Strong, shipped: {tier('strong')}
Familiar, working knowledge: {tier('familiar')}
Not yet theirs but learnable: {tier('learnable')}
</candidate_profile>

<cv>
{cv_text.strip()[:12000]}
</cv>

<job>
Title: {job.role_title}
Company: {job.company}
Location: {job.location} ({job.work_mode})
Type: {job.vacancy_type}
Posted: {job.posted_at or 'unknown'} | Applicants: {job.applicants if job.applicants is not None else 'unknown'}
Salary as advertised: {job.salary or 'not stated'}

Description:
{jd[:14000]}
</job>

Score this job for this candidate."""


def score_one(
    client,
    job: Job,
    cv_text: str,
    skills: SkillsProfile,
    model: str,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One structured-JSON call. Raises on API failure; the caller degrades."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(job, cv_text, skills)},
        ],
        response_format=response_format or JSON_SCHEMA_FORMAT,
        temperature=0.2,
        max_tokens=1500,
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def score_with_fallback(
    client,
    job: Job,
    cv_text: str,
    skills: SkillsProfile,
    model: str,
    response_format: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Score one job, downgrading json_schema -> json_object once if rejected.

    Returns (result, format that worked) so the caller stops paying for a
    rejected request on every remaining job.
    """
    import openai

    try:
        return score_one(client, job, cv_text, skills, model, response_format), response_format
    except openai.BadRequestError:
        if response_format is not JSON_SCHEMA_FORMAT:
            raise
        log.warning("llm: %s rejected json_schema - falling back to json_object mode", model)
        result = score_one(client, job, cv_text, skills, model, JSON_OBJECT_FORMAT)
        return result, JSON_OBJECT_FORMAT


def apply_result(job: Job, result: dict[str, Any], filters: Filters | None = None) -> None:
    """Overlay a model verdict onto a rule-scored job, defensively."""
    try:
        score = int(result.get("score", job.rule_score))
    except (TypeError, ValueError):
        score = job.rule_score
    job.match_score = max(0, min(100, score))
    job.verdict = verdict_for(job.match_score, filters)
    if result.get("matched_skills"):
        job.matched_skills = [str(s) for s in result["matched_skills"]][:12]
    if result.get("missing_skills"):
        job.missing_skills = [str(s) for s in result["missing_skills"]][:12]
    if result.get("why_matched"):
        job.why_matched = str(result["why_matched"]).strip()
    if result.get("jd_summary"):
        job.jd_summary = str(result["jd_summary"]).strip()
    seniority = result.get("seniority")
    if seniority and seniority != "Unknown":
        job.seniority = seniority


def score_jobs(
    jobs: list[Job],
    settings: Settings,
    cv_text: str,
    skills: SkillsProfile,
    filters: Filters | None = None,
) -> int:
    """Rescore the shortlist with the model. Returns how many were rescored."""
    if not settings.llm_enabled or not jobs:
        return 0
    if not settings.openai_api_key:
        log.warning("llm: LLM_ENABLED is set but OPENAI_API_KEY is empty - keeping rule scores")
        return 0

    try:
        import openai
    except ImportError:
        log.warning("llm: the openai package is not installed - keeping rule scores")
        return 0

    client = openai.OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.llm_base_url or None,
        max_retries=3,
        timeout=60.0,
    )
    shortlist = sorted(jobs, key=lambda j: j.rule_score, reverse=True)[: settings.llm_top_n]
    response_format: dict[str, Any] = JSON_SCHEMA_FORMAT
    rescored = 0

    for job in shortlist:
        cached = _read_cache(job.job_id)
        if cached:
            apply_result(job, cached, filters)
            rescored += 1
            continue
        try:
            result, response_format = score_with_fallback(
                client, job, cv_text, skills, settings.llm_model, response_format
            )
        except openai.RateLimitError:
            log.warning("llm: rate limited - stopping AI scoring, rule scores stand")
            break
        except openai.BadRequestError as exc:
            log.warning("llm: bad request for job %s: %s", job.job_id, exc)
            continue
        except openai.APIConnectionError as exc:
            log.warning("llm: connection error (%r) - stopping AI scoring", exc)
            break
        except openai.APIStatusError as exc:
            log.warning("llm: API error %s for job %s - keeping rule score", exc.status_code, job.job_id)
            continue
        except json.JSONDecodeError:
            log.warning("llm: job %s came back as non-JSON - keeping rule score", job.job_id)
            continue
        except Exception as exc:
            log.warning("llm: unexpected failure on job %s: %r", job.job_id, exc)
            continue

        _write_cache(job.job_id, result)
        apply_result(job, result, filters)
        rescored += 1

    log.info("llm: rescored %d/%d shortlisted job(s) with %s", rescored, len(shortlist), settings.llm_model)
    return rescored
