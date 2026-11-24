from datetime import datetime, timezone

from conftest import FakeHTTP, fixture_bytes, make_response
from scraper.http import PoliteSession
from scraper.sources.rss import RssSource, decode_feed, parse_rss, split_title_company

MJM_URL = "https://www.myjobmag.com/jobsxml.xml"
HNJ_URL = "https://www.hotnigerianjobs.com/feed/rss.xml"


def test_parse_myjobmag_feed():
    items = parse_rss(fixture_bytes("myjobmag_feed.xml"))
    assert len(items) == 5
    first = items[0]
    assert first["title"] == "Client Account Manager at LawPavilion Business Solutions"
    assert first["link"] == "https://www.myjobmag.com/jobs/client-account-manager-at-lawpavilion-business-solutions-2"
    assert first["extra"]["industry"] == "Law / Legal"
    assert first["pub_date"] == datetime(2026, 11, 24, 16, 59, 10, tzinfo=timezone.utc)
    # The feed is double-encoded at source; we repair "weâ€™re" -> "we’re"
    text = " ".join(i["description"] for i in items)
    assert "we’re" in text and "â" not in text


def test_parse_hotnigerianjobs_feed():
    items = parse_rss(fixture_bytes("hotnigerianjobs_feed.xml"))
    assert len(items) == 5
    assert items[0]["title"].startswith("Clinical Programmes Officer at Congenital Heart Disease")
    assert "Lagos State" in items[0]["description"]
    # +0100 converted to UTC
    assert items[0]["pub_date"] == datetime(2026, 11, 23, 17, 24, 3, tzinfo=timezone.utc)


def test_decode_feed_prefers_utf8_over_wrong_declaration():
    data = '<?xml version="1.0" encoding="iso-8859-1"?><rss><channel><item><title>Salary ₦100,000</title></item></channel></rss>'.encode()
    assert parse_rss(data)[0]["title"] == "Salary ₦100,000"
    latin = '<?xml version="1.0" encoding="iso-8859-1"?><rss><channel><item><title>Caf\xe9</title></item></channel></rss>'.encode("latin-1")
    assert b"Caf\xc3\xa9" in decode_feed(latin)


def test_split_title_company():
    assert split_title_company("Field Credit Officer (Edo) at Moniepoint Incorporated - 3 Openings") == (
        "Field Credit Officer (Edo)", "Moniepoint Incorporated")
    assert split_title_company("Accountant") == ("Accountant", None)


def _source(cfg_extra, feed_name, url):
    cfg = {"name": "x", "prefix": "x", "url": url, **cfg_extra}
    origin = url.split("/", 3)[:3]
    routes = {"/".join(origin) + "/robots.txt": make_response(200, "User-agent: *\nDisallow: /*?\n"),
              url: make_response(200, fixture_bytes(feed_name))}
    return RssSource(cfg), PoliteSession("JobAfriqBot/1.0", session=FakeHTTP(routes), sleep=lambda s: None)


def test_rss_source_ids_and_seen():
    src, sess = _source({"prefix": "hnj", "id_pattern": r"/hotjobs/(\d+)/"}, "hotnigerianjobs_feed.xml", HNJ_URL)
    posts = list(src.fetch(sess, seen={"hnj-996322"}))
    ids = [p.job_id for p in posts]
    assert ids[0] == "hnj-996323" and "hnj-996322" not in ids and len(ids) == 4
    p = posts[0]
    assert p.company_hint == "Congenital Heart Disease Foundation of Nigeria (CHDFNigeria)"
    assert p.title == "Clinical Programmes Officer" and p.trusted


def test_rss_source_hash_ids_are_stable():
    src, sess = _source({"prefix": "mjm"}, "myjobmag_feed.xml", MJM_URL)
    ids1 = [p.job_id for p in src.fetch(sess, seen=set())]
    src2, sess2 = _source({"prefix": "mjm"}, "myjobmag_feed.xml", MJM_URL)
    ids2 = [p.job_id for p in src2.fetch(sess2, seen=set())]
    assert ids1 == ids2 and all(i.startswith("mjm-") and len(i) == 14 for i in ids1)


def test_rss_source_bad_feed_logs_error():
    cfg = {"name": "bad", "prefix": "bad", "url": "https://bad.example/feed"}
    routes = {"https://bad.example/robots.txt": make_response(404),
              "https://bad.example/feed": make_response(200, b"<html>not a feed")}
    src = RssSource(cfg)
    sess = PoliteSession("JobAfriqBot/1.0", session=FakeHTTP(routes), sleep=lambda s: None)
    assert list(src.fetch(sess, seen=set())) == []
    assert src.errors


def test_generic_jobs_at_title_kept_whole():
    assert split_title_company("Jobs at Carlson Hotel") == ("Jobs at Carlson Hotel", "Carlson Hotel")
