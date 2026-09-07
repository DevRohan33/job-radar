"""Reads profile/skills.yaml and profile/filters.yaml into typed objects.

Everything tunable lives in those two files - skill tiers and their weights, the
hard gates, the rubric weights and the verdict bands. This module is the only
place that knows their shape, so the scorer never touches raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import PROFILE_DIR

# Skill ids are snake_case; these read better in the sheet. Anything not listed
# falls back to a title-cased version of the id.
DISPLAY_NAMES: dict[str, str] = {
    "ai": "AI", "aws": "AWS", "c_lang": "C", "ci_cd": "CI/CD", "etl": "ETL",
    "fastapi": "FastAPI", "graphql": "GraphQL", "huggingface": "Hugging Face",
    "llm": "LLM", "mcp": "MCP", "mongodb": "MongoDB", "multi_agent": "Multi-agent",
    "nextjs": "Next.js", "nodejs": "Node.js", "openai_api": "OpenAI API",
    "postgresql": "PostgreSQL", "prompt_engineering": "Prompt engineering",
    "rag": "RAG", "react": "React", "rest_api": "REST API", "s3": "S3",
    "sql": "SQL", "system_design": "System design", "threejs": "Three.js",
    "typescript": "TypeScript", "vector_db": "Vector DB", "ci": "CI",
    "nlp": "NLP", "go": "Go", "numpy": "NumPy", "pytest": "pytest",
}

# The skills that make a role an AI role rather than a plain backend one.
AI_SIGNAL_SKILLS = {
    "rag", "llm", "langchain", "langgraph", "multi_agent", "vector_db",
    "prompt_engineering", "openai_api", "mcp", "evals", "huggingface",
}

UNPAID_MARKERS = (
    "unpaid", "equity only", "equity-only", "no salary", "stipend only",
    "voluntary", "pro bono", "profit share only",
)

DEFAULT_WEIGHTS = {
    "must_have_skills": 33.0,
    "seniority_fit": 15.0,
    "adjacent_skills": 13.0,
    "ai_domain_bonus": 12.0,
    "freshness": 13.0,
    "location_mode": 9.0,
    "compensation": 5.0,
}

DEFAULT_BANDS = {"apply_now": 85, "strong": 70, "worth_a_look": 55}


def display(skill_id: str) -> str:
    if skill_id in DISPLAY_NAMES:
        return DISPLAY_NAMES[skill_id]
    return skill_id.replace("_", " ").strip().capitalize()


@dataclass
class SkillTier:
    name: str
    weight: float
    skills: list[str] = field(default_factory=list)


@dataclass
class SkillsProfile:
    candidate: dict[str, Any] = field(default_factory=dict)
    tiers: dict[str, SkillTier] = field(default_factory=dict)
    tracked_gaps: list[str] = field(default_factory=list)
    synonyms: dict[str, list[str]] = field(default_factory=dict)

    def tier(self, name: str) -> SkillTier:
        return self.tiers.get(name, SkillTier(name, 0.0, []))

    def skills_in(self, *tier_names: str) -> list[str]:
        out: list[str] = []
        for name in tier_names:
            for skill in self.tier(name).skills:
                if skill not in out:
                    out.append(skill)
        return out

    @property
    def owned(self) -> list[str]:
        """What you can claim today. `learnable` is deliberately excluded."""
        return self.skills_in("core", "strong", "familiar")

    @property
    def all_known(self) -> list[str]:
        return self.skills_in("core", "strong", "familiar", "learnable")

    def weight_of(self, skill: str) -> float:
        for tier in self.tiers.values():
            if skill in tier.skills:
                return tier.weight
        return 0.0

    @property
    def aliases(self) -> dict[str, list[str]]:
        """skill id -> every spelling to look for. The id itself matches too:
        `rest_api` is normalized to "rest api" / "rest-api" / "restapi"."""
        return {skill: list(self.synonyms.get(skill, [])) for skill in self.all_known}

    @property
    def years_experience(self) -> float:
        start = self.candidate.get("experience_start")
        if not start:
            return 0.0
        try:
            began = date.fromisoformat(str(start))
        except ValueError:
            return 0.0
        return round((date.today() - began).days / 365.25, 1)


@dataclass
class Filters:
    primary_roles: list[str] = field(default_factory=list)
    secondary_roles: list[str] = field(default_factory=list)
    title_blocklist: list[str] = field(default_factory=list)
    onsite_cities: list[str] = field(default_factory=list)
    remote_scopes: list[str] = field(default_factory=lambda: ["india", "global"])
    reject_onsite_elsewhere: bool = True
    work_mode_order: list[str] = field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    max_years_required: int | None = None
    reject_at_or_above: int | None = None
    accept_internships: bool = False
    accept_contract: bool = True
    hard_floor_lpa: float | None = None
    reference_lpa: float | None = None
    reject_unpaid: bool = True
    company_blocklist: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    bands: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_BANDS))
    max_jd_fetches_per_run: int = 15
    min_delay_seconds: float = 4.0
    max_delay_seconds: float = 11.0
    skip_if_rule_score_below: int = 0

    def weight(self, key: str) -> float:
        return float(self.weights.get(key, DEFAULT_WEIGHTS.get(key, 0.0)))


def _lower_list(values: Any) -> list[str]:
    return [str(v).strip().lower() for v in (values or []) if str(v).strip()]


def parse_skills(data: dict[str, Any]) -> SkillsProfile:
    tiers: dict[str, SkillTier] = {}
    for name in ("core", "strong", "familiar", "learnable"):
        block = data.get(name) or {}
        if isinstance(block, list):  # tolerate a bare list of skills
            block = {"weight": 1.0, "skills": block}
        tiers[name] = SkillTier(
            name=name,
            weight=float(block.get("weight", 0.0) or 0.0),
            skills=_lower_list(block.get("skills")),
        )
    synonyms = {
        str(k).strip().lower(): [str(v) for v in (aliases or [])]
        for k, aliases in (data.get("synonyms") or {}).items()
    }
    return SkillsProfile(
        candidate=data.get("candidate") or {},
        tiers=tiers,
        tracked_gaps=_lower_list(data.get("tracked_gaps")),
        synonyms=synonyms,
    )


def parse_filters(data: dict[str, Any]) -> Filters:
    roles = data.get("target_roles") or {}
    locations = data.get("locations") or {}
    experience = data.get("experience") or {}
    compensation = data.get("compensation") or {}
    enrichment = data.get("enrichment") or {}

    weights = dict(DEFAULT_WEIGHTS)
    for key, value in (data.get("weights") or {}).items():
        weights[str(key)] = float(value)

    bands = dict(DEFAULT_BANDS)
    for key, value in (data.get("verdict_bands") or {}).items():
        bands[str(key)] = int(value)

    return Filters(
        primary_roles=[str(r) for r in (roles.get("primary") or [])],
        secondary_roles=[str(r) for r in (roles.get("secondary") or [])],
        title_blocklist=_lower_list(data.get("title_must_not_contain")),
        onsite_cities=_lower_list(locations.get("accept_onsite")),
        remote_scopes=_lower_list(locations.get("accept_remote_scopes")) or ["india", "global"],
        reject_onsite_elsewhere=bool(locations.get("reject_if_onsite_elsewhere", True)),
        work_mode_order=_lower_list(data.get("work_mode_preference")) or ["remote", "hybrid", "onsite"],
        max_years_required=experience.get("max_years_required"),
        reject_at_or_above=experience.get("reject_at_or_above"),
        accept_internships=bool(experience.get("accept_internships", False)),
        accept_contract=bool(experience.get("accept_contract", True)),
        hard_floor_lpa=compensation.get("hard_floor_lpa"),
        reference_lpa=compensation.get("reference_lpa"),
        reject_unpaid=bool(compensation.get("reject_unpaid", True)),
        company_blocklist=[str(c) for c in (data.get("company_blocklist") or [])],
        weights=weights,
        bands=bands,
        max_jd_fetches_per_run=int(enrichment.get("max_jd_fetches_per_run", 15)),
        min_delay_seconds=float(enrichment.get("min_delay_seconds", 4)),
        max_delay_seconds=float(enrichment.get("max_delay_seconds", 11)),
        skip_if_rule_score_below=int(enrichment.get("skip_if_rule_score_below", 0)),
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - see SETUP.md.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping.")
    return data


@lru_cache(maxsize=1)
def load_skills(path: str | None = None) -> SkillsProfile:
    return parse_skills(_read_yaml(Path(path) if path else PROFILE_DIR / "skills.yaml"))


@lru_cache(maxsize=1)
def load_filters(path: str | None = None) -> Filters:
    return parse_filters(_read_yaml(Path(path) if path else PROFILE_DIR / "filters.yaml"))


@lru_cache(maxsize=1)
def load_cv(path: str | None = None) -> str:
    target = Path(path) if path else PROFILE_DIR / "cv.md"
    return target.read_text(encoding="utf-8") if target.exists() else ""
