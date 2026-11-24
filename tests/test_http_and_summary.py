import json

import pytest
import requests

from conftest import FakeHTTP, make_response
from scraper.http import FetchError, PoliteSession, RobotsRules
from scraper.summarize import Summarizer, copies_source, template_summary


# --------------------------------------------------------------------------- robots / http

def test_robots_wildcards_and_agent_groups():
    txt = """
User-agent: *
Disallow: /*?
Disallow: /apply-now/
Allow: /apply-now/info$

User-agent: JobAfriqBot
Disallow: /private
"""
    star = RobotsRules(txt, "SomeOtherBot/2.0")
    assert star.allowed("/jobsxml.xml")
    assert not star.allowed("/search?q=x")
    assert not star.allowed("/apply-now/123")
    assert star.allowed("/apply-now/info")
    ours = RobotsRules(txt, "JobAfriqBot/1.0 (free job alerts)")
    assert not ours.allowed("/private/x") and ours.allowed("/search?q=x")


def test_retries_at_most_twice_then_gives_up():
    sleeps = []
    fake = FakeHTTP({
        "https://a.test/robots.txt": make_response(404),
        "https://a.test/x": [requests.ConnectionError("boom")],
    })
    s = PoliteSession("JobAfriqBot/1.0", delay_seconds=(1.5, 2.0), max_retries=2, session=fake, sleep=sleeps.append)
    with pytest.raises(FetchError):
        s.get("https://a.test/x")
    assert fake.calls.count("https://a.test/x") == 3  # 1 try + 2 retries
    assert all(1.4 <= w <= 2.0 for w in sleeps)       # polite delay between requests


def test_retry_then_success_and_user_agent():
    fake = FakeHTTP({
        "https://a.test/robots.txt": make_response(200, "User-agent: *\nAllow: /\n"),
        "https://a.test/x": [make_response(503), make_response(200, "ok")],
    })
    s = PoliteSession("JobAfriqBot/1.0", session=fake, sleep=lambda _: None)
    assert s.get("https://a.test/x").text == "ok"
    assert fake.headers["User-Agent"] == "JobAfriqBot/1.0"


# --------------------------------------------------------------------------- summaries

JOB = {"title": "Accountant", "company": "Greenfield Foods Limited", "location": "Ikeja, Lagos",
       "job_type": "Full-time", "experience": "3+ years", "salary": "₦250,000 per month", "deadline": "2026-11-30"}


def test_template_summary():
    s = template_summary(JOB)
    assert s == ("Greenfield Foods Limited is hiring an Accountant in Ikeja, Lagos. "
                 "3+ years experience required; pay: ₦250,000 per month; apply by 2026-11-30.")
    assert len(s) <= 320
    assert template_summary({"title": "Driver", "company": None, "location": "Remote"}) == "An employer is hiring a Driver remotely."


def test_copies_source_detects_verbatim_text():
    src = "Interested and qualified candidates should send their CV to careers@example.com using the job title."
    assert copies_source("Interested and qualified candidates should send their CV to careers@example.com now", src)
    assert not copies_source("Greenfield wants an accountant in Lagos with 3 years' experience.", src)


class FakeGemini:
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.calls = status, payload, []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json))
        body = {"candidates": [{"content": {"parts": [{"text": self.payload}]}}]} if self.payload else {}
        return make_response(self.status, __import__("json").dumps(body))


def test_gemini_used_when_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    fake = FakeGemini(payload=json.dumps({"summary": "Greenfield wants an accountant in Ikeja.", "title": "Accountant"}))
    s = Summarizer(session=fake, sleep=lambda _: None)
    out = s.ai_fields("Accountant", "long advert text")
    assert out["summary"] == "Greenfield wants an accountant in Ikeja."
    url, headers, _ = fake.calls[0]
    assert "gemini-test:generateContent" in url and headers["x-goog-api-key"] == "test-key"


def test_gemini_failure_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    s = Summarizer(session=FakeGemini(status=403), sleep=lambda _: None)
    assert s.ai_fields("Accountant", "text") is None
    assert not s.enabled  # stops calling for the rest of the run


def test_gemini_copied_summary_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    text = "Interested and qualified candidates should send their CV to careers@example.com using the job title as subject."
    s = Summarizer(session=FakeGemini(payload=json.dumps({"summary": text})), sleep=lambda _: None)
    assert s.ai_fields("Accountant", text)["summary"] is None


def test_no_key_means_no_calls(no_gemini):
    s = Summarizer()
    assert not s.enabled and s.ai_fields("x", "y") is None


def test_template_summary_handles_unknown_company_and_noisy_titles():
    from scraper.summarize import role_from_title
    job = {"title": "Business Development Executive Needed In Lagos", "company": "Not stated", "location": "Lagos"}
    assert template_summary(job) == "An employer is hiring a Business Development Executive in Lagos."
    assert role_from_title("HIRING - Recruitment Intern") == "Recruitment Intern"
    assert role_from_title("Urgent Recruitment - Marketer") == "Marketer"
    assert role_from_title("Job Vacancy - Warehouse Cleaner (Agbara)") == "Warehouse Cleaner (Agbara)"
    assert role_from_title("Experienced Driver Wanted In Port Harcourt") == "Experienced Driver"
