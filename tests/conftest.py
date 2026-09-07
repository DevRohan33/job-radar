from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from job_radar.profile import Filters, SkillsProfile, parse_filters, parse_skills  # noqa: E402
from tests.make_fixture import build  # noqa: E402

# A miniature version of profile/skills.yaml and profile/filters.yaml, in the
# same shape. Tests use these so tuning your real profile never breaks the suite.
SKILLS_YAML = {
    "candidate": {"name": "Test Candidate", "experience_start": "2025-02-01"},
    "core": {"weight": 1.0, "skills": ["python", "fastapi", "rag", "llm", "rest_api", "postgresql"]},
    "strong": {"weight": 0.75, "skills": ["multi_agent", "vector_db", "pandas", "aws", "docker"]},
    "familiar": {"weight": 0.4, "skills": ["kubernetes", "ci_cd", "sql"]},
    "learnable": {"weight": 0.25, "skills": ["typescript", "nodejs", "kafka", "go"]},
    "tracked_gaps": ["typescript", "nodejs", "kafka", "go"],
    "synonyms": {
        "python": ["python3"],
        "rag": ["retrieval augmented generation", "retrieval-augmented generation"],
        "llm": ["large language model", "large language models", "genai", "generative ai"],
        "rest_api": ["rest apis", "restful"],
        "multi_agent": ["multi-agent", "agentic", "tool calling"],
        "vector_db": ["vector database", "pinecone", "pgvector"],
        "postgresql": ["postgres"],
        "nodejs": ["node.js", "node"],
        "go": ["golang"],
        "ci_cd": ["ci/cd", "github actions"],
    },
}

FILTERS_YAML = {
    "target_roles": {
        "primary": ["AI Engineer", "LLM Engineer", "Machine Learning Engineer"],
        "secondary": ["Backend Engineer", "Python Developer"],
    },
    "title_must_not_contain": ["senior", "lead", "manager", "frontend", "devops", "sales"],
    "locations": {
        "accept_onsite": ["Bangalore", "Bengaluru", "Hyderabad", "Pune", "Noida"],
        "accept_remote_scopes": ["india"],
        "reject_if_onsite_elsewhere": True,
    },
    "work_mode_preference": ["remote", "hybrid", "onsite"],
    "experience": {
        "max_years_required": 3,
        "reject_at_or_above": 4,
        "accept_internships": False,
        "accept_contract": True,
    },
    "compensation": {"hard_floor_lpa": None, "reference_lpa": 12, "reject_unpaid": True},
    "company_blocklist": [],
    "weights": {
        "must_have_skills": 33,
        "seniority_fit": 15,
        "adjacent_skills": 13,
        "ai_domain_bonus": 12,
        "freshness": 13,
        "location_mode": 9,
        "compensation": 5,
    },
    "verdict_bands": {"apply_now": 85, "strong": 70, "worth_a_look": 55},
    "enrichment": {
        "max_jd_fetches_per_run": 15,
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
        "skip_if_rule_score_below": 45,
    },
}


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    directory = ROOT / "tests" / "fixtures"
    sample = directory / "sample_alert.eml"
    if not sample.exists():
        build(sample)
    return directory


@pytest.fixture
def skills() -> SkillsProfile:
    return parse_skills(SKILLS_YAML)


@pytest.fixture
def filters() -> Filters:
    return parse_filters(FILTERS_YAML)
