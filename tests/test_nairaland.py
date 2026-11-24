from datetime import datetime, timezone

from conftest import NOW, FakeHTTP, fixture_text, make_response
from scraper.http import PoliteSession
from scraper.sources.nairaland import (
    NairalandSource, decode_cfemail, parse_board, parse_nairaland_time, parse_thread,
)

BASE = "https://www.nairaland.com"


def test_parse_board_lists_threads_in_order():
    threads = parse_board(fixture_text("nairaland_board.html"), BASE)
    ids = [t["thread_id"] for t in threads]
    assert ids == ["8110001", "8123456", "8123499", "8123500", "8123510"]
    acc = threads[1]
    assert acc["title"] == "Accountant Needed At Greenfield Foods Limited, Ikeja"
    assert acc["url"] == f"{BASE}/8123456/accountant-needed-greenfield-foods-ikeja"
    # Shouty titles get tidied
    assert threads[2]["title"].startswith("Urgent Hiring!!! Customer Care Reps")


def test_parse_board_fallback_without_td_ids():
    html = '<table summary="links"><tr><td><b><a href="/7777777/some-job-title">Some Job</a></b></td></tr></table>'
    assert parse_board(html, BASE)[0]["thread_id"] == "7777777"


def test_parse_legit_thread_first_post_only():
    post = parse_thread(fixture_text("nairaland_thread_legit.html"), NOW)
    assert post["title"] == "Accountant Needed At Greenfield Foods Limited, Ikeja"
    assert post["post_id"] == "130001111"
    # Cloudflare-obfuscated e-mail decoded
    assert "careers@greenfieldfoods.com.ng" in post["text"]
    assert "Deadline: 30th November, 2026." in post["text"]
    # Replies (2nd post) are not included
    assert "HND accepted" not in post["text"]
    assert "08038110001" not in post["text"]
    # 10:15am WAT on Nov 23 == 09:15 UTC
    assert post["posted_at"] == datetime(2026, 11, 23, 9, 15, tzinfo=timezone.utc)


def test_parse_time_variants():
    assert parse_nairaland_time("10:15am On Nov 23, 2025", NOW) == datetime(2025, 11, 23, 9, 15, tzinfo=timezone.utc)
    assert parse_nairaland_time("8:02pm On Nov 22", NOW) == datetime(2026, 11, 22, 19, 2, tzinfo=timezone.utc)
    assert parse_nairaland_time("7:30am", NOW) == datetime(2026, 11, 24, 6, 30, tzinfo=timezone.utc)
    # A December date seen in late November belongs to last year
    assert parse_nairaland_time("9:00am On Dec 20", NOW).year == 2025
    assert parse_nairaland_time("no time here", NOW) is None


def test_decode_cfemail():
    assert decode_cfemail("8cefedfee9e9feffccebfee9e9e2eae5e9e0e8eae3e3e8ffa2efe3e1a2e2eb") == "careers@greenfieldfoods.com.ng"
    assert decode_cfemail("zz") == ""


def _session(routes):
    return PoliteSession("JobAfriqBot/1.0", session=FakeHTTP(routes), sleep=lambda s: None)


ROBOTS = make_response(200, "User-agent: *\nDisallow: /login\nDisallow: /nigeria?\n")


def test_source_fetch_skips_seen_and_filtered_threads():
    routes = {
        f"{BASE}/robots.txt": ROBOTS,
        f"{BASE}/jobs": make_response(200, fixture_text("nairaland_board.html")),
        f"{BASE}/jobs/1": make_response(200, "<html><body>empty</body></html>"),
        f"{BASE}/8123456/accountant-needed-greenfield-foods-ikeja": make_response(200, fixture_text("nairaland_thread_legit.html")),
        f"{BASE}/8123499/urgent-hiring-customer-care-reps-salary": make_response(200, fixture_text("nairaland_thread_scam.html")),
        # 8123510 -> 404 (fetch error, must not crash and must not be marked seen)
    }
    sess = _session(routes)
    src = NairalandSource({"name": "nairaland", "prefix": "nl", "base_url": BASE, "pages": 2,
                           "max_threads": 40, "skip_title_patterns": [r"\brules\b", r"\bi need (a )?job\b"]})
    posts = list(src.fetch(sess, seen={"nl-8123499"}))
    assert [p.job_id for p in posts] == ["nl-8123456"]
    fetched = sess.session.calls
    assert f"{BASE}/8123499/urgent-hiring-customer-care-reps-salary" not in fetched  # seen
    assert not any("8110001" in u or "8123500" in u for u in fetched)               # filtered titles
    assert {"nl-8110001", "nl-8123500", "nl-8123456"} <= src.handled_ids
    assert "nl-8123510" not in src.handled_ids
    assert any("8123510" in e for e in src.errors)


def test_source_respects_thread_cap():
    routes = {
        f"{BASE}/robots.txt": ROBOTS,
        f"{BASE}/jobs": make_response(200, fixture_text("nairaland_board.html")),
        f"{BASE}/8123456/accountant-needed-greenfield-foods-ikeja": make_response(200, fixture_text("nairaland_thread_legit.html")),
    }
    sess = _session(routes)
    src = NairalandSource({"name": "nairaland", "prefix": "nl", "base_url": BASE, "pages": 1, "max_threads": 1})
    list(src.fetch(sess, seen=set()))
    thread_calls = [u for u in sess.session.calls if u.count("/") > 3]
    assert len(thread_calls) == 1


def test_cloudflare_challenge_is_reported_not_crashing():
    challenge = make_response(403, fixture_text("cloudflare_challenge.html"), {"cf-mitigated": "challenge"})
    sess = _session({f"{BASE}/robots.txt": ROBOTS, f"{BASE}/jobs": challenge})
    src = NairalandSource({"name": "nairaland", "prefix": "nl", "base_url": BASE, "pages": 2})
    assert list(src.fetch(sess, seen=set())) == []
    assert len(src.errors) == 1 and "bot challenge" in src.errors[0]
    assert len(sess.session.calls) == 2  # robots + first board page only, no retries


def test_robots_disallow_is_respected():
    sess = _session({f"{BASE}/robots.txt": make_response(200, "User-agent: *\nDisallow: /jobs\n")})
    src = NairalandSource({"name": "nairaland", "prefix": "nl", "base_url": BASE, "pages": 1})
    assert list(src.fetch(sess, seen=set())) == []
    assert sess.session.calls == [f"{BASE}/robots.txt"]
    assert "robots.txt disallows" in src.errors[0]


def test_old_bumped_threads_are_skipped_by_id_gap():
    routes = {
        f"{BASE}/robots.txt": ROBOTS,
        f"{BASE}/jobs": make_response(200, fixture_text("nairaland_board.html")),
    }
    sess = _session(routes)
    src = NairalandSource({"name": "nairaland", "prefix": "nl", "base_url": BASE, "pages": 1,
                           "max_id_gap": 10, "skip_title_patterns": [r"\brules\b"]})
    list(src.fetch(sess, seen=set()))
    # newest thread is 8123510; with a gap of 10 only 8123500+ are considered
    thread_calls = [u for u in sess.session.calls if u.count("/") > 3]
    assert thread_calls == [f"{BASE}/8123500/i-need-a-job-urgently", f"{BASE}/8123510/driver-wanted-port-harcourt"]


# ---------------------------------------------------------------- real saved pages (redacted)

def test_real_board_page_parses_all_threads():
    threads = parse_board(fixture_text("nairaland_board_real.html"), BASE)
    assert len(threads) == 65
    ids = {t["thread_id"] for t in threads}
    assert {"7688963", "8788250", "7730180"} <= ids  # incl. slugs with dots
    assert all(t["url"].startswith(f"{BASE}/{t['thread_id']}/") for t in threads)
    assert all(t["title"] and not t["title"].isdigit() for t in threads)


def test_real_thread_page_parses_first_post(no_gemini):
    from scraper.jobs import build_job
    from scraper.sources.base import RawPost
    from scraper.summarize import Summarizer

    post = parse_thread(fixture_text("nairaland_thread_real.html"), NOW)
    assert post["title"] == "HIRING - Recruitment Intern"
    assert post["post_id"] == "141258613"
    assert post["posted_at"] == datetime(2026, 11, 24, 7, 16, tzinfo=timezone.utc)
    assert post["text"].startswith("JOB TITLE: Recruitment Intern")
    raw = RawPost(job_id="nl-8788226", title=post["title"], text=post["text"], url=f"{BASE}/8788226/x",
                  source="nairaland", posted_at=post["posted_at"], links=post["links"])
    job = build_job(raw, Summarizer(), "Nairaland", NOW)
    assert job["company"] == "FMR Agency"
    assert job["category"] == "Internship/Graduate"
    assert job["job_type"] == "Internship"
    assert job["location"].startswith("Lagos, Abuja")
    assert "recruiter@fmragency.example" in job["how_to_apply"]
    assert job["red_flags"] == []
