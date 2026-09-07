"""AI scoring layer, exercised entirely offline with a fake OpenAI client."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from job_radar.models import Job
from job_radar.score import llm

RESULT = {
    "score": 88,
    "matched_skills": ["Python", "FastAPI"],
    "missing_skills": ["Kubernetes"],
    "why_matched": "Builds the exact RAG services this team runs.",
    "jd_summary": "Backend role owning retrieval pipelines.",
    "seniority": "Mid",
}


class FakeClient:
    """Records the request and replays a canned completion."""

    def __init__(self, payload=RESULT, fail_on_schema=False):
        self.payload = payload
        self.fail_on_schema = fail_on_schema
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on_schema and kwargs["response_format"]["type"] == "json_schema":
            raise openai.BadRequestError(
                "response_format json_schema is not supported",
                response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com")),
                body=None,
            )
        message = SimpleNamespace(content=json.dumps(self.payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_job() -> Job:
    job = Job(job_id="1", role_title="Backend Engineer", company="Acme", jd_text="Build RAG services in FastAPI.")
    job.rule_score = 61
    job.match_score = 61
    return job


def test_prompt_carries_cv_jd_and_profile(skills):
    prompt = llm.build_prompt(make_job(), "I built FastAPI RAG pipelines.", skills)
    assert "Backend Engineer" in prompt
    assert "Build RAG services in FastAPI." in prompt
    assert "I built FastAPI RAG pipelines." in prompt
    assert "Python, FastAPI" in prompt  # core tier from the fixture profile


def test_score_one_requests_strict_json_schema(skills):
    client = FakeClient()
    result = llm.score_one(client, make_job(), "cv", skills, "gpt-4o-mini")
    assert result == RESULT
    sent = client.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


def test_schema_rejection_falls_back_to_json_object(skills):
    client = FakeClient(fail_on_schema=True)
    result, fmt = llm.score_with_fallback(
        client, make_job(), "cv", skills, "gpt-4o-mini", llm.JSON_SCHEMA_FORMAT
    )
    assert result == RESULT
    assert fmt is llm.JSON_OBJECT_FORMAT
    # The caller keeps the working format, so later jobs never retry the rejection.
    assert [c["response_format"]["type"] for c in client.calls] == ["json_schema", "json_object"]


def test_apply_result_overrides_the_rule_score():
    job = make_job()
    llm.apply_result(job, RESULT)
    assert job.match_score == 88
    assert job.verdict == "Apply now"
    assert job.matched_skills == ["Python", "FastAPI"]
    assert job.missing_skills == ["Kubernetes"]
    assert job.seniority == "Mid"
    assert job.why_matched.startswith("Builds the exact")


@pytest.mark.parametrize("bad", [{"score": 150}, {"score": -5}, {"score": "high"}, {}])
def test_apply_result_survives_a_bad_score(bad):
    job = make_job()
    llm.apply_result(job, bad)
    assert 0 <= job.match_score <= 100


def test_scoring_is_skipped_without_a_key(skills, monkeypatch):
    from job_radar.config import Settings

    settings = Settings()
    settings.llm_enabled = True
    settings.openai_api_key = ""
    assert llm.score_jobs([make_job()], settings, "cv", skills) == 0


def test_results_are_cached_so_a_rerun_costs_nothing(skills, monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path)
    from job_radar.config import Settings

    settings = Settings()
    settings.llm_enabled = True
    settings.openai_api_key = "sk-test"
    settings.llm_model = "gpt-4o-mini"

    client = FakeClient()
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)

    jobs = [make_job()]
    assert llm.score_jobs(jobs, settings, "cv", skills) == 1
    assert jobs[0].match_score == 88
    assert len(client.calls) == 1

    fresh = [make_job()]
    assert llm.score_jobs(fresh, settings, "cv", skills) == 1
    assert fresh[0].match_score == 88
    assert len(client.calls) == 1  # served from cache, no second request
