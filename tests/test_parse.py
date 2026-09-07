"""Parser regression tests - the safety net for the one component LinkedIn owns.

When yield drops to zero, save a fresh alert email into tests/fixtures and add
its expectations here before touching the parser.
"""

from __future__ import annotations

from datetime import date

from job_radar.sources.gmail_linkedin import load_fixtures
from job_radar.sources.parse import parse_email, parse_html, parse_text, resolve_posted_at
from job_radar.textutil import clean_apply_url, extract_job_id


def test_every_fixture_yields_cards(fixtures_dir):
    emails = load_fixtures(fixtures_dir)
    assert emails, "no .eml fixtures found"
    for email in emails:
        assert parse_email(email), f"zero cards parsed from {email.subject!r}"


def test_fields_extracted_from_sample(fixtures_dir):
    (email,) = [e for e in load_fixtures(fixtures_dir) if "sample" in e.message_id or True][:1]
    cards = {c.job_id: c for c in parse_email(email)}

    assert set(cards) == {"4021998877", "4033112244", "4044556677"}

    card = cards["4021998877"]
    assert card.role_title == "Python Developer"
    assert card.company == "Northwind Analytics"
    assert "Bengaluru" in card.location
    assert card.applicants_hint == 43
    assert card.posted_hint == "2 days ago"
    assert card.salary_hint == "8 - 14 LPA"
    assert card.apply_url == "https://www.linkedin.com/jobs/view/4021998877/"


def test_alert_name_becomes_source(fixtures_dir):
    email = load_fixtures(fixtures_dir)[0]
    assert email.alert_name == "python developer"
    assert all(c.source == "python developer" for c in parse_email(email))


def test_logo_anchor_does_not_become_the_company():
    html = """
    <table><tr><td>
      <a href="https://www.linkedin.com/comm/jobs/view/999888777/?trackingId=x"><img src="logo.png"></a>
      <a href="https://www.linkedin.com/comm/jobs/view/999888777/?trackingId=x">Backend Engineer</a>
      <p>Acme Corp &middot; Hyderabad, India (Remote)</p>
    </td></tr></table>"""
    (card,) = parse_html(html, "test")
    assert card.role_title == "Backend Engineer"
    assert card.company == "Acme Corp"
    assert card.location == "Hyderabad, India (Remote)"


def test_text_fallback_when_html_is_missing(fixtures_dir):
    email = load_fixtures(fixtures_dir)[0]
    cards = parse_text(email.text, "fallback")
    assert {c.job_id for c in cards} == {"4021998877", "4033112244"}
    assert cards[0].role_title == "Python Developer"


def test_parse_never_raises_on_garbage():
    class Broken:
        html = "<html><a href='https://www.linkedin.com/comm/jobs/view/'>x</a>"
        text = ""
        subject = "broken"

    assert parse_email(Broken()) == []


def test_job_id_extraction_variants():
    assert extract_job_id("https://www.linkedin.com/comm/jobs/view/4021998877/?trk=x") == "4021998877"
    assert extract_job_id("https://www.linkedin.com/jobs/view/senior-dev-at-acme-4021998877") == "4021998877"
    assert extract_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4021998877") == "4021998877"
    assert extract_job_id("https://www.linkedin.com/feed/") is None


def test_apply_url_is_stripped_of_tracking():
    dirty = "https://www.linkedin.com/comm/jobs/view/4021998877/?trackingId=aB%3D%3D&refId=9&midToken=z"
    assert clean_apply_url(dirty) == "https://www.linkedin.com/jobs/view/4021998877/"


def test_posted_at_is_relative_to_the_email_date():
    received = date(2026, 9, 7)
    assert resolve_posted_at("2 days ago", received) == date(2026, 9, 5)
    assert resolve_posted_at("21 hours ago", received) == received
    assert resolve_posted_at("1 week ago", received) == date(2026, 8, 31)
    assert resolve_posted_at("", received) is None
