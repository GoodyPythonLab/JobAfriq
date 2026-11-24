from conftest import NOW, fixture_text
from scraper import redflags as rf
from scraper.jobs import FIELDS, build_job
from scraper.sources.base import RawPost
from scraper.sources.nairaland import parse_thread
from scraper.summarize import Summarizer


def _nl_post(fixture, job_id, url):
    p = parse_thread(fixture_text(fixture), NOW)
    return RawPost(job_id=job_id, title=p["title"], text=p["text"], url=url, source="nairaland",
                   posted_at=p["posted_at"], links=p["links"])


def test_scam_post_gets_all_expected_flags(no_gemini):
    raw = _nl_post("nairaland_thread_scam.html", "nl-8123499", "https://www.nairaland.com/8123499/x")
    job = build_job(raw, Summarizer(), "Nairaland", NOW)
    flags = job["red_flags"]
    assert rf.FLAG_FEE in flags
    assert rf.FLAG_FREE_EMAIL in flags
    assert rf.FLAG_HIGH_PAY in flags
    assert rf.FLAG_NO_COMPANY in flags
    assert rf.FLAG_OVERSEAS in flags
    assert job["company"] == "Not stated"
    assert job["category"] == "Customer Service"
    assert job["salary"] == "₦750,000 per month"


def test_whatsapp_only_contact_flag():
    flags = rf.detect_red_flags(text="Driver needed at Swift Movers Ltd. WhatsApp 08123456789 for details",
                                company="Swift Movers Ltd", salary_monthly=None, has_official_link=False)
    assert flags == [rf.FLAG_WHATSAPP]
    flags = rf.detect_red_flags(text="Driver needed at Swift Movers Ltd. Call 08123456789",
                                company="Swift Movers Ltd", salary_monthly=None, has_official_link=False)
    assert flags == [rf.FLAG_PHONE]


def test_legit_post_has_no_flags_and_full_fields(no_gemini):
    raw = _nl_post("nairaland_thread_legit.html", "nl-8123456",
                   "https://www.nairaland.com/8123456/accountant-needed-greenfield-foods-ikeja")
    job = build_job(raw, Summarizer(), "Nairaland", NOW)
    assert list(job) == FIELDS
    assert job["red_flags"] == []  # "We do not charge any fee" must not trigger the fee flag
    assert job["id"] == "nl-8123456"
    assert job["company"] == "Greenfield Foods Limited"
    assert job["location"] == "Ikeja, Lagos"
    assert job["category"] == "Accounting"
    assert job["job_type"] == "Full-time"
    assert job["experience"] == "3+ years"
    assert job["salary"] == "₦250,000 - ₦300,000 per month"
    assert job["deadline"] == "2026-11-30"
    assert job["posted_at"] == "2026-11-23T09:15:00Z"
    assert "careers@greenfieldfoods.com.ng" in job["how_to_apply"]
    # Summary is short and in our own words, never the post itself
    assert len(job["summary"]) <= 320
    assert "Greenfield Foods Limited is hiring" in job["summary"]
    assert "Interested and qualified candidates" not in job["summary"]


def test_trusted_feed_job_is_not_flagged_for_contact(no_gemini):
    raw = RawPost(job_id="hnj-1", title="Field Credit Officer (Edo)", url="https://x/1", source="hotnigerianjobs",
                  text="Moniepoint Incorporated is recruiting to fill the position of: Field Credit Officer. "
                       "The position is located in Edo State. Minimum of 2 years experience.",
                  posted_at=NOW, company_hint="Moniepoint Incorporated", trusted=True)
    job = build_job(raw, Summarizer(), "HotNigerianJobs", NOW)
    assert job["red_flags"] == []
    assert job["location"] == "Edo"
    assert job["category"] == "Accounting"
    assert job["how_to_apply"] == "Open the full ad on HotNigerianJobs for application details."
