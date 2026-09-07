"""Gate and rubric tests. These are the assertions that keep the score honest."""

from __future__ import annotations

from datetime import date, timedelta

from job_radar.models import Job
from job_radar.score.rules import (
    analyze_skills,
    check_gates,
    infer_seniority,
    infer_vacancy_type,
    infer_work_mode,
    salary_lpa,
    score_job,
    summarize,
    verdict_for,
    years_required,
)

JD = """
We are hiring an AI Engineer to build RAG pipelines in production.

Requirements:
- 2+ years of experience with Python and FastAPI
- Retrieval augmented generation, vector databases (pgvector or Pinecone)
- Comfortable with REST APIs and PostgreSQL
- Nice to have: Node.js, Kafka, Kubernetes
"""


def make_job(**kwargs) -> Job:
    base = dict(
        job_id="1",
        role_title="AI Engineer",
        company="Northwind Analytics",
        location="Bengaluru, India (Hybrid)",
        posted_at=date.today() - timedelta(days=1),
        applicants=20,
    )
    base.update(kwargs)
    job = Job(**base)
    return job


def enriched(**kwargs) -> Job:
    job = make_job(jd_text=JD, **kwargs)
    job.enriched = True
    return job


# --- inference ---------------------------------------------------------------


def test_seniority_inference():
    assert infer_seniority("Senior Backend Engineer") == "Senior"
    assert infer_seniority("Junior AI Engineer") == "Junior"
    assert infer_seniority("Software Engineering Intern") == "Intern"
    assert infer_seniority("Staff Software Engineer") == "Staff+"
    assert infer_seniority("Software Engineer", "requires 7 years of experience") == "Senior"


def test_work_mode_and_vacancy_inference():
    assert infer_work_mode("Bengaluru, India (Remote)") == "Remote"
    assert infer_work_mode("Pune (Hybrid)") == "Hybrid"
    assert infer_work_mode("Mumbai") == "Unknown"
    assert infer_vacancy_type("Data Science Internship") == "Internship"
    assert infer_vacancy_type("Backend Engineer", "This is a full-time role") == "Full-time"


def test_years_required():
    assert years_required("2+ years of experience with Python") == 2
    assert years_required("We want 5-8 years experience") == 5
    assert years_required("no numbers here") is None


def test_salary_normalization():
    assert salary_lpa("8 - 14 LPA") == (8.0, 14.0)
    assert salary_lpa("Rs 800,000 - 1,200,000") == (8.0, 12.0)
    assert salary_lpa("not stated") is None


# --- gates -------------------------------------------------------------------


def test_blocked_title_term_is_gated_out(filters):
    result = check_gates(make_job(role_title="Senior AI Engineer"), filters)
    assert not result.passed and "senior" in result.reason


def test_blocklist_matches_whole_words_only(filters):
    # "lead" is blocked; "Leadership" in a title is not the same thing.
    assert check_gates(make_job(role_title="AI Engineer, Leadership Team"), filters).passed
    assert not check_gates(make_job(role_title="Lead AI Engineer"), filters).passed


def test_experience_ceiling_is_gated_out(filters):
    result = check_gates(make_job(jd_text="We need 5 years of experience."), filters)
    assert not result.passed and "years" in result.reason


def test_three_years_is_allowed_but_four_is_not(filters):
    assert check_gates(make_job(jd_text="3 years of experience required"), filters).passed
    assert not check_gates(make_job(jd_text="4 years of experience required"), filters).passed


def test_internships_are_gated_out(filters):
    result = check_gates(make_job(role_title="AI Engineering Intern"), filters)
    assert not result.passed and "internship" in result.reason


def test_contract_roles_are_allowed(filters):
    assert check_gates(make_job(role_title="AI Engineer (Contract)"), filters).passed


def test_company_blocklist(filters):
    filters.company_blocklist = ["Northwind"]
    result = check_gates(make_job(), filters)
    assert not result.passed and "blocklist" in result.reason


def test_onsite_outside_accepted_cities_is_gated_out(filters):
    result = check_gates(make_job(location="Kolkata, India", work_mode="On-site"), filters)
    assert not result.passed and "accepted city" in result.reason


def test_onsite_in_an_accepted_city_passes(filters):
    assert check_gates(make_job(location="Hyderabad, India", work_mode="On-site"), filters).passed


def test_remote_skips_the_city_gate_but_not_the_country_one(filters):
    assert check_gates(make_job(location="Remote, India", work_mode="Remote"), filters).passed
    result = check_gates(make_job(location="Remote (United States)", work_mode="Remote"), filters)
    assert not result.passed and "scoped to" in result.reason


def test_global_remote_scope_accepts_anywhere(filters):
    filters.remote_scopes = ["india", "global"]
    assert check_gates(make_job(location="Remote (Germany)", work_mode="Remote"), filters).passed


def test_unpaid_roles_are_gated_out(filters):
    result = check_gates(make_job(jd_text="This is an unpaid position with equity only."), filters)
    assert not result.passed and "unpaid" in result.reason


def test_a_good_job_passes_every_gate(filters):
    assert check_gates(enriched(), filters).passed


# --- skills ------------------------------------------------------------------


def test_skill_analysis_splits_owned_from_missing(skills):
    analysis = analyze_skills(JD, skills)
    assert "python" in analysis["matched"]
    assert "fastapi" in analysis["matched"]
    assert "postgresql" in analysis["matched"]
    assert "nodejs" in analysis["missing"]
    assert "kafka" in analysis["missing"]
    assert "python" not in analysis["missing"]


def test_synonyms_collapse_to_one_skill(skills):
    for spelling in ("Retrieval Augmented Generation", "retrieval-augmented generation", "RAG"):
        assert "rag" in analyze_skills(spelling, skills)["matched"]
    for spelling in ("Large Language Models", "GenAI", "generative AI"):
        assert "llm" in analyze_skills(spelling, skills)["matched"]


def test_underscore_ids_match_natural_spellings(skills):
    assert "rest_api" in analyze_skills("You will design REST APIs.", skills)["matched"]
    assert "vector_db" in analyze_skills("experience with pgvector", skills)["matched"]
    assert "ci_cd" in analyze_skills("CI/CD with GitHub Actions", skills)["matched"]


def test_two_letter_ids_need_an_alias_not_prose(skills):
    # "we go to production weekly" must not count as the Go language.
    assert "go" not in analyze_skills("We go to production weekly.", skills)["matched"]
    assert "go" in analyze_skills("Golang services in production", skills)["missing"]


def test_ai_signal_skills_are_detected(skills):
    analysis = analyze_skills(JD, skills)
    assert set(analysis["ai_hit"]) >= {"rag", "vector_db"}


# --- scoring -----------------------------------------------------------------


def test_enriched_match_scores_above_the_same_job_unenriched(skills, filters):
    thin = score_job(make_job(), skills, filters, cv_text="Python FastAPI RAG")
    rich = score_job(enriched(job_id="2"), skills, filters, cv_text="Python FastAPI RAG engineer")
    assert rich.match_score > thin.match_score


def test_a_primary_target_role_beats_an_unrelated_title(skills, filters):
    ai = score_job(enriched(), skills, filters)
    other = score_job(enriched(job_id="2", role_title="Business Analyst"), skills, filters)
    assert ai.match_score > other.match_score
    assert ai.score_breakdown["ai_domain_bonus"] > other.score_breakdown["ai_domain_bonus"]


def test_freshness_beats_a_stale_identical_posting(skills, filters):
    fresh = score_job(enriched(posted_at=date.today()), skills, filters)
    stale = score_job(
        enriched(job_id="2", posted_at=date.today() - timedelta(days=20), applicants=400),
        skills,
        filters,
    )
    assert fresh.match_score > stale.match_score


def test_remote_outscores_onsite_all_else_equal(skills, filters):
    remote = score_job(enriched(work_mode="Remote"), skills, filters)
    onsite = score_job(enriched(job_id="2", work_mode="On-site"), skills, filters)
    assert remote.score_breakdown["location_mode"] > onsite.score_breakdown["location_mode"]


def test_weights_are_read_from_the_profile(skills, filters):
    filters.weights["freshness"] = 0
    job = score_job(enriched(), skills, filters)
    assert job.score_breakdown["freshness"] == 0


def test_score_is_bounded_and_explained(skills, filters):
    job = score_job(enriched(), skills, filters, cv_text="Python FastAPI RAG LangChain")
    assert 0 <= job.match_score <= 100
    assert job.why_matched
    assert job.jd_summary
    assert abs(sum(job.score_breakdown.values()) - job.match_score) < 1.5
    assert set(job.score_breakdown) == set(filters.weights)


def test_skills_reach_the_sheet_with_readable_names(skills, filters):
    job = score_job(enriched(), skills, filters)
    assert "FastAPI" in job.matched_skills
    assert "RAG" in job.matched_skills
    assert "REST API" in job.matched_skills
    assert "Node.js" in job.missing_skills


def test_verdict_bands_come_from_the_profile(filters):
    assert verdict_for(92, filters) == "Apply now"
    assert verdict_for(85, filters) == "Apply now"
    assert verdict_for(70, filters) == "Strong"
    assert verdict_for(55, filters) == "Worth a look"
    assert verdict_for(54, filters) == "Archive"

    filters.bands["apply_now"] = 90
    assert verdict_for(88, filters) == "Strong"


def test_summary_is_compressed():
    assert len(summarize(JD).split()) <= 41
