"""Stage 4 - Score (free tier): gates first, then the weighted rubric.

Both the gates and the weights come from profile/filters.yaml, and the skill
tiers from profile/skills.yaml - nothing here is hardcoded policy. Every
component is recorded in `job.score_breakdown`, so when a score looks wrong you
can see exactly which weight or tier produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from ..models import Job, Seniority, Verdict
from ..profile import AI_SIGNAL_SKILLS, UNPAID_MARKERS, Filters, SkillsProfile, display
from ..textutil import find_skills, normalize, tfidf_cosine
from .vocab import COMMON_SKILLS, DEFAULT_ALIASES

SENIORITY_ORDER: list[Seniority] = ["Intern", "Junior", "Mid", "Senior", "Staff+"]

SENIORITY_TITLE_PATTERNS: list[tuple[str, Seniority]] = [
    (r"\b(intern|internship|trainee|apprentice)\b", "Intern"),
    (r"\b(junior|jr\.?|entry[- ]level|graduate|fresher|associate)\b", "Junior"),
    (r"\b(principal|staff|architect|distinguished|head of|director|vp\b|chief)\b", "Staff+"),
    (r"\b(senior|sr\.?|lead|iii|iv)\b", "Senior"),
]

WORK_MODE_PATTERNS = [
    (r"\b(fully remote|work from home|wfh|100% remote|remote[- ]first|remote)\b", "Remote"),
    (r"\b(hybrid|flexible.{0,12}(office|onsite))\b", "Hybrid"),
    (r"\b(on[- ]?site|in[- ]office|work from office|wfo)\b", "On-site"),
]

VACANCY_PATTERNS = [
    (r"\b(internship|intern)\b", "Internship"),
    (r"\b(contract|contractor|freelance|c2h|consultant|temporary)\b", "Contract"),
    (r"\b(part[- ]time)\b", "Part-time"),
    (r"\b(full[- ]time|permanent|fte)\b", "Full-time"),
]

YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*(?:\+)?\s*(?:years?|yrs?)\b",
    re.I,
)

# "12-18 LPA", "Rs 8,00,000 - 12,00,000", "$120k - $160k"
LPA_RANGE = re.compile(r"([\d.]+)\s*(?:-|–|to)\s*([\d.]+)\s*(?:lpa|lakhs?|l\b)", re.I)
NUM_RANGE = re.compile(
    r"(?:₹|rs\.?|inr|\$|usd|€|£)?\s?([\d,.]+)\s*(k)?\s*(?:-|–|to)\s*"
    r"(?:₹|rs\.?|inr|\$|usd|€|£)?\s?([\d,.]+)\s*(k)?",
    re.I,
)

# Used only to spot a remote role that is remote *somewhere else*.
FOREIGN_MARKERS = re.compile(
    r"\b(united states|usa|u\.s\.|canada|united kingdom|\buk\b|europe|germany|"
    r"france|netherlands|poland|australia|singapore|dubai|uae|japan|brazil|"
    r"mexico|emea|apac(?!\s*incl)|latam|us[- ]only|eu[- ]only)\b",
    re.I,
)

# How many core skills in one JD count as full marks. A posting never asks for
# your whole stack, so requiring that would cap every score.
CORE_TARGET_SHARE = 0.4
ADJACENT_TARGET = 3.0
AI_TARGET = 3.0


@dataclass
class GateResult:
    passed: bool
    reason: str = ""


# --- inference ---------------------------------------------------------------


def infer_seniority(title: str, jd: str = "") -> Seniority:
    haystack = f" {title.lower()} "
    for pattern, level in SENIORITY_TITLE_PATTERNS:
        if re.search(pattern, haystack, re.I):
            return level
    years = years_required(jd or title)
    if years is not None:
        if years <= 2:
            return "Junior"
        if years <= 5:
            return "Mid"
        if years <= 8:
            return "Senior"
        return "Staff+"
    return "Mid" if jd else "Unknown"


def infer_work_mode(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for pattern, mode in WORK_MODE_PATTERNS:
        if re.search(pattern, blob, re.I):
            return mode
    return "Unknown"


def infer_vacancy_type(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for pattern, kind in VACANCY_PATTERNS:
        if re.search(pattern, blob, re.I):
            return kind
    return "Unknown"


def years_required(text: str) -> int | None:
    """Lowest bound of the first plausible experience requirement in the text."""
    if not text:
        return None
    for m in YEARS.finditer(text):
        low = int(m.group(1))
        if low > 25:
            continue
        window = text[max(0, m.start() - 60) : m.end() + 60].lower()
        if "experience" in window or "exp" in window or "yrs" in window:
            return low
    return None


def salary_lpa(text: str) -> tuple[float, float] | None:
    """Normalize a salary string to (min, max) in LPA. None when unparseable."""
    if not text:
        return None
    m = LPA_RANGE.search(text)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            return None
    m = NUM_RANGE.search(text)
    if not m:
        return None
    try:
        low = float(m.group(1).replace(",", ""))
        high = float(m.group(3).replace(",", ""))
    except ValueError:
        return None
    if m.group(2):
        low *= 1000
    if m.group(4):
        high *= 1000
    if re.search(r"[$]|usd", text, re.I):  # rough, only feeds the comp signal
        low, high = low * 83, high * 83
    if high < 100:  # already in lakhs
        return low, high
    return round(low / 100000, 2), round(high / 100000, 2)


def prepare(job: Job) -> Job:
    """Fill in the inferred fields the gates need. Safe to call twice."""
    if job.work_mode == "Unknown":
        job.work_mode = infer_work_mode(job.location, job.role_title, job.jd_text)  # type: ignore[assignment]
    if job.vacancy_type == "Unknown":
        job.vacancy_type = infer_vacancy_type(job.role_title, job.jd_text)  # type: ignore[assignment]
    if job.seniority == "Unknown":
        job.seniority = infer_seniority(job.role_title, job.jd_text)
    return job


# --- skills ------------------------------------------------------------------


def _key(skill: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", skill.lower())


def _outside_vocabulary(skills: SkillsProfile) -> list[str]:
    """Baseline skills that are not part of your taxonomy under any spelling.

    These are what make `missing_skills` a real gap list rather than just
    "things absent from my CV".
    """
    known = {_key(s) for s in skills.all_known}
    for skill, aliases in skills.synonyms.items():
        known.add(_key(skill))
        known.update(_key(a) for a in aliases)
    return [s for s in COMMON_SKILLS if _key(s) not in known]


def analyze_skills(text: str, skills: SkillsProfile) -> dict[str, list[str]]:
    """What the JD asks for, split into what you have and what you don't."""
    aliases = skills.aliases
    owned_hits = find_skills(text, skills.owned, aliases)
    learnable_hits = find_skills(text, skills.tier("learnable").skills, aliases)
    gap_hits = find_skills(
        text, [g for g in skills.tracked_gaps if g not in learnable_hits], aliases
    )
    outside_hits = find_skills(text, _outside_vocabulary(skills), DEFAULT_ALIASES)

    missing: list[str] = []
    for skill in learnable_hits + gap_hits:
        if skill not in missing:
            missing.append(skill)

    return {
        "matched": owned_hits,
        "missing": missing,
        "missing_outside": outside_hits,
        "core_hit": [s for s in skills.tier("core").skills if s in owned_hits],
        "adjacent_hit": [s for s in skills.tier("familiar").skills if s in owned_hits] + learnable_hits,
        "ai_hit": [s for s in owned_hits + learnable_hits if s in AI_SIGNAL_SKILLS],
    }


# --- gates -------------------------------------------------------------------


def _phrase(term: str) -> re.Pattern[str]:
    """Whole-phrase matcher: 'lead' must not match 'leadership'."""
    body = re.escape(term.strip())
    lead = r"(?<![a-z0-9])" if term[:1].isalnum() else ""
    trail = r"(?![a-z0-9])" if term[-1:].isalnum() else ""
    return re.compile(lead + body + trail, re.I)


def _first_match(terms: Iterable[str], text: str) -> str | None:
    for term in terms:
        if term and _phrase(term).search(text):
            return term
    return None


def check_gates(job: Job, filters: Filters) -> GateResult:
    """Hard filters. A gate failure is a rejection, not a low score."""
    prepare(job)
    title = job.role_title or ""
    blob = f"{title}\n{job.company}\n{job.location}\n{job.jd_text}"

    hit = _first_match(filters.title_blocklist, title)
    if hit:
        return GateResult(False, f"title contains {hit!r}")

    hit = _first_match(filters.company_blocklist, job.company)
    if hit:
        return GateResult(False, f"company on blocklist ({hit})")

    if not filters.accept_internships and (
        job.vacancy_type == "Internship" or job.seniority == "Intern"
    ):
        return GateResult(False, "internship")

    if not filters.accept_contract and job.vacancy_type == "Contract":
        return GateResult(False, "contract role")

    gate = _location_gate(job, filters)
    if not gate.passed:
        return gate

    if filters.reject_at_or_above is not None:
        needed = years_required(blob)
        if needed is not None and needed >= int(filters.reject_at_or_above):
            return GateResult(False, f"requires {needed}+ years (reject at {filters.reject_at_or_above})")

    if filters.reject_unpaid:
        low = normalize(blob)
        for marker in UNPAID_MARKERS:
            if marker in low:
                return GateResult(False, f"unpaid ({marker})")

    if filters.hard_floor_lpa:
        parsed = salary_lpa(job.salary) or salary_lpa(job.jd_text)
        if parsed and parsed[1] < float(filters.hard_floor_lpa):
            return GateResult(False, f"max comp {parsed[1]} LPA below floor {filters.hard_floor_lpa}")

    return GateResult(True)


def _location_gate(job: Job, filters: Filters) -> GateResult:
    location = job.location or ""
    if job.work_mode == "Remote":
        if "global" in filters.remote_scopes:
            return GateResult(True)
        foreign = FOREIGN_MARKERS.search(location)
        if foreign and not re.search(r"\bindia\b", location, re.I):
            return GateResult(False, f"remote but scoped to {foreign.group(0)}")
        return GateResult(True)

    if job.work_mode in ("On-site", "Hybrid") and filters.reject_onsite_elsewhere:
        if not filters.onsite_cities or not location:
            return GateResult(True)
        if not _first_match(filters.onsite_cities, location):
            return GateResult(False, f"{job.work_mode.lower()} in {location!r}, not an accepted city")
    return GateResult(True)


# --- scoring -----------------------------------------------------------------


def _freshness_factor(job: Job) -> float:
    if not job.posted_at:
        return 0.6
    age = max(0, (date.today() - job.posted_at).days)
    if age <= 1:
        return 1.0
    if age <= 3:
        return 0.85
    if age <= 7:
        return 0.65
    if age <= 14:
        return 0.4
    return 0.2


def _applicant_factor(job: Job) -> float:
    if job.applicants is None:
        return 0.85
    if job.applicants <= 25:
        return 1.0
    if job.applicants <= 75:
        return 0.85
    if job.applicants <= 200:
        return 0.6
    return 0.35


def _title_role_factor(title: str, filters: Filters) -> float:
    if _first_match(filters.primary_roles, title):
        return 1.0
    if _first_match(filters.secondary_roles, title):
        return 0.55
    return 0.0


def _seniority_factor(job: Job, filters: Filters) -> float:
    years = years_required(f"{job.role_title}\n{job.jd_text}")
    if years is None:
        years_factor = 0.7  # most JDs that stay silent are open on experience
    elif filters.max_years_required is not None and years <= int(filters.max_years_required):
        years_factor = 1.0
    elif filters.reject_at_or_above is not None and years < int(filters.reject_at_or_above):
        years_factor = 0.5
    else:
        years_factor = 0.25
    band_factor = {"Junior": 1.0, "Mid": 0.9, "Intern": 0.5, "Senior": 0.3, "Staff+": 0.15}.get(
        job.seniority, 0.7
    )
    return min(years_factor, band_factor)


def _mode_factor(job: Job, filters: Filters) -> float:
    key = {"Remote": "remote", "Hybrid": "hybrid", "On-site": "onsite"}.get(job.work_mode)
    if key is None or key not in filters.work_mode_order:
        return 0.6
    index = filters.work_mode_order.index(key)
    return max(0.4, 1.0 - 0.22 * index)


def _comp_factor(job: Job, filters: Filters) -> tuple[float, tuple[float, float] | None]:
    parsed = salary_lpa(job.salary) or salary_lpa(job.jd_text)
    if parsed is None:
        return 0.5, None  # unstated is the norm in Indian listings, not a penalty
    reference = float(filters.reference_lpa or 0)
    if not reference or parsed[1] >= reference:
        return 1.0, parsed
    if parsed[1] >= reference * 0.75:
        return 0.7, parsed
    return 0.35, parsed


def _weighted_share(hits: list[str], skills: SkillsProfile, target: float) -> float:
    """Tier-weighted hit count, normalized against a realistic target."""
    if target <= 0:
        return 0.0
    total = sum(skills.weight_of(s) for s in hits)
    return min(1.0, total / target)


def score_job(job: Job, skills: SkillsProfile, filters: Filters, cv_text: str = "") -> Job:
    """Fill match_score, verdict, matched/missing skills and why_matched, in place."""
    prepare(job)
    text = f"{job.role_title}\n{job.company}\n{job.location}\n{job.jd_text}".strip()
    analysis = analyze_skills(text, skills)

    core = skills.tier("core").skills
    core_target = max(3.0, len(core) * CORE_TARGET_SHARE)
    detected = analysis["matched"] + analysis["missing"] + analysis["missing_outside"]

    # 1. Must-have skills: how much of your core the JD wants, and how much of
    #    what the JD wants you actually have.
    yours_covered = min(1.0, len(analysis["core_hit"]) / core_target)
    jd_covered = (
        sum(skills.weight_of(s) for s in analysis["matched"]) / len(detected) if detected else 0.0
    )
    must_component = filters.weight("must_have_skills") * (
        0.6 * yours_covered + 0.4 * min(1.0, jd_covered)
    )
    if not job.enriched:
        # Title-only evidence is weak evidence; it must not manufacture a top score.
        must_component *= 0.7

    # 2. Seniority fit, from the experience block.
    seniority_component = filters.weight("seniority_fit") * _seniority_factor(job, filters)

    # 3. Adjacent / learnable skills, plus overall CV<->JD similarity.
    adjacent_share = _weighted_share(analysis["adjacent_hit"], skills, ADJACENT_TARGET)
    similarity = tfidf_cosine(cv_text, job.jd_text or job.role_title) if cv_text else 0.0
    adjacent_component = filters.weight("adjacent_skills") * (
        0.55 * adjacent_share + 0.45 * min(1.0, similarity * 2.5)
    )

    # 4. AI domain bonus: your actual edge. A primary-target title alone earns it.
    title_factor = _title_role_factor(job.role_title, filters)
    ai_share = min(1.0, len(analysis["ai_hit"]) / AI_TARGET)
    ai_component = filters.weight("ai_domain_bonus") * min(1.0, title_factor + 0.5 * ai_share)

    # 5. Freshness & competition.
    freshness_component = filters.weight("freshness") * _freshness_factor(job) * _applicant_factor(job)

    # 6. Location & work mode.
    location_component = filters.weight("location_mode") * _mode_factor(job, filters)

    # 7. Compensation signal.
    comp_factor, parsed_salary = _comp_factor(job, filters)
    comp_component = filters.weight("compensation") * comp_factor
    if parsed_salary and not job.salary:
        job.salary = f"{parsed_salary[0]}-{parsed_salary[1]} LPA"

    breakdown = {
        "must_have_skills": round(must_component, 2),
        "seniority_fit": round(seniority_component, 2),
        "adjacent_skills": round(adjacent_component, 2),
        "ai_domain_bonus": round(ai_component, 2),
        "freshness": round(freshness_component, 2),
        "location_mode": round(location_component, 2),
        "compensation": round(comp_component, 2),
    }

    job.score_breakdown = breakdown
    job.match_score = max(0, min(100, int(round(sum(breakdown.values())))))
    job.rule_score = job.match_score
    job.matched_skills = [display(s) for s in analysis["matched"][:12]]
    job.missing_skills = [display(s) for s in analysis["missing"]][:8] + analysis["missing_outside"][:4]
    job.verdict = verdict_for(job.match_score, filters)
    job.why_matched = job.why_matched or explain(job, analysis, title_factor)
    job.jd_summary = job.jd_summary or summarize(job.jd_text)
    return job


def verdict_for(score: int, filters: Filters | None = None) -> Verdict:
    bands = filters.bands if filters else {"apply_now": 85, "strong": 70, "worth_a_look": 55}
    if score >= bands.get("apply_now", 85):
        return "Apply now"
    if score >= bands.get("strong", 70):
        return "Strong"
    if score >= bands.get("worth_a_look", 55):
        return "Worth a look"
    return "Archive"


def explain(job: Job, analysis: dict[str, list[str]], title_factor: float = 0.0) -> str:
    """One sentence. The column that makes the score trustworthy."""
    bits: list[str] = []
    if title_factor >= 1.0:
        bits.append("primary target role")
    if analysis["core_hit"]:
        bits.append("core match on " + ", ".join(display(s) for s in analysis["core_hit"][:3]))
    elif analysis["matched"]:
        bits.append("overlaps on " + ", ".join(display(s) for s in analysis["matched"][:3]))
    else:
        bits.append("no listed skill overlap detected")
    if job.seniority != "Unknown":
        bits.append(f"{job.seniority.lower()} band")
    if job.work_mode != "Unknown":
        bits.append(job.work_mode.lower())
    if job.posted_at:
        age = (date.today() - job.posted_at).days
        bits.append("posted today" if age <= 0 else f"posted {age}d ago")
    if job.applicants is not None:
        bits.append(f"{job.applicants} applicants")
    if not job.enriched:
        bits.append("scored from the alert email only")
    gaps = [display(s) for s in analysis["missing"][:2]]
    tail = f"; gaps: {', '.join(gaps)}" if gaps else ""
    return (" · ".join(bits) + tail).strip()


def summarize(jd_text: str, words: int = 40) -> str:
    if not jd_text:
        return ""
    cleaned = re.sub(r"\s+", " ", jd_text).strip()
    cleaned = re.sub(r"^(about (the|us)|job description|overview)[:\s-]*", "", cleaned, flags=re.I)
    tokens = cleaned.split(" ")
    return " ".join(tokens[:words]) + ("..." if len(tokens) > words else "")
