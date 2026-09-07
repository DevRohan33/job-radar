"""End-to-end tests over the fixture inbox, with no network and no credentials."""

from __future__ import annotations

from job_radar import pipeline
from job_radar.config import Settings
from job_radar.enrich.linkedin_guest import JobDetail, parse_job_page, read_cache, write_cache
from job_radar.models import SHEET_COLUMNS, Job, job_to_row
from job_radar.sinks import csv_sink


def _settings() -> Settings:
    s = Settings()
    s.enrich_enabled = False
    s.llm_enabled = False
    return s


def _use_test_profile(monkeypatch, skills, filters):
    """Pin the pipeline to the fixture profile.

    Without this the end-to-end tests read whatever is in profile/, so tuning
    your own skills.yaml would break the suite.
    """
    monkeypatch.setattr(pipeline, "load_skills", lambda: skills)
    monkeypatch.setattr(pipeline, "load_filters", lambda: filters)
    monkeypatch.setattr(pipeline, "load_cv", lambda: "Python FastAPI RAG engineer")


def test_dry_run_scores_the_fixture_inbox(fixtures_dir, monkeypatch, tmp_path, skills, filters):
    _use_test_profile(monkeypatch, skills, filters)
    monkeypatch.setattr(csv_sink, "DEFAULT_PATH", tmp_path / "jobs.csv")
    monkeypatch.setattr(pipeline, "STATE_DIR", tmp_path)
    report = pipeline.run(_settings(), pipeline.RunOptions(fixtures=str(fixtures_dir), dry_run=True))

    assert report.emails == 1
    assert report.cards == 3
    assert report.unique == 3
    assert report.gated_out >= 1  # the sales role fails title_must_not_contain
    assert report.scored >= 1
    assert report.published == 0
    assert not report.zero_yield


def test_csv_sink_dedups_across_runs(fixtures_dir, monkeypatch, tmp_path, skills, filters):
    _use_test_profile(monkeypatch, skills, filters)
    csv_path = tmp_path / "jobs.csv"
    monkeypatch.setattr(csv_sink, "DEFAULT_PATH", csv_path)
    monkeypatch.setattr(pipeline, "STATE_DIR", tmp_path)
    options = pipeline.RunOptions(fixtures=str(fixtures_dir), csv_only=True)

    first = pipeline.run(_settings(), options)
    assert first.published >= 1

    second = pipeline.run(_settings(), options)
    assert second.published == 0
    # Only what was actually written counts as known; gated-out jobs never got a row.
    assert second.already_known == first.published


def test_row_serialization_matches_the_header():
    job = Job(job_id="1", role_title="Dev", matched_skills=["Python", "SQL"])
    row = job_to_row(job)
    assert len(row) == len(SHEET_COLUMNS)
    assert row[SHEET_COLUMNS.index("matched_skills")] == "Python, SQL"
    assert row[SHEET_COLUMNS.index("status")] == "New"
    assert row[SHEET_COLUMNS.index("notes")] == ""


def test_zero_yield_is_detected():
    report = pipeline.RunReport(emails=3, cards=0)
    assert report.zero_yield
    assert not pipeline.RunReport(emails=0, cards=0).zero_yield


# --- enrichment (offline) ----------------------------------------------------

GUEST_HTML = """
<section>
  <div class="show-more-less-html__markup">
    <p>We are hiring a Python Developer.</p><p>Requirements: 3 years of experience.</p>
  </div>
  <ul>
    <li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Seniority level</h3>
      <span class="description__job-criteria-text">Mid-Senior level</span>
    </li>
    <li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Employment type</h3>
      <span class="description__job-criteria-text">Full-time</span>
    </li>
  </ul>
  <span>87 applicants</span>
</section>"""


def test_guest_page_parsing():
    detail = parse_job_page(GUEST_HTML, "123")
    assert detail.ok
    assert "Python Developer" in detail.description
    assert detail.seniority == "Mid-Senior level"
    assert detail.employment_type == "Full-time"
    assert detail.applicants == 87


def test_jd_cache_roundtrip(tmp_path):
    detail = JobDetail(job_id="555", description="hello", ok=True)
    write_cache(detail, tmp_path)
    assert read_cache("555", tmp_path).description == "hello"
    assert read_cache("nope", tmp_path) is None


def test_enrichment_failure_degrades_instead_of_raising(monkeypatch, tmp_path):
    from job_radar.enrich import linkedin_guest

    def boom(*args, **kwargs):
        raise RuntimeError("HTTP 999")

    monkeypatch.setattr(linkedin_guest, "fetch_job_detail", boom)
    settings = Settings()
    settings.enrich_enabled = True
    settings.enrich_min_delay = settings.enrich_max_delay = 0.0
    jobs = [Job(job_id=str(i), role_title="Dev") for i in range(5)]

    report = linkedin_guest.enrich_jobs(jobs, settings, cache_dir=tmp_path)

    assert report.failed >= 1
    assert report.blocked  # stopped early rather than hammering
    assert all(not j.enriched for j in jobs)
