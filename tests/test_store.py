import json
from datetime import timedelta

from conftest import NOW
from scraper.jobs import iso_utc
from scraper.store import (
    load_jobs, load_seen, merge_jobs, prune_jobs, update_seen, write_pages, write_seen,
)


def job(i, hours_ago=0, title=None, company="Acme Ltd", location="Lagos", deadline=None):
    return {
        "id": f"t-{i}", "title": title or f"Job {i}", "company": company, "location": location,
        "category": "Other", "job_type": "Full-time", "experience": "Not specified", "salary": "₦100,000 per month",
        "summary": "s", "how_to_apply": "h", "deadline": deadline,
        "posted_at": iso_utc(NOW - timedelta(hours=hours_ago)), "source": "test", "source_url": "u", "red_flags": [],
    }


def test_duplicates_removed_by_normalised_title_company_location():
    a = job(1, 5, title="Sales Executive", company="Acme Ltd", location="Lagos")
    b = job(2, 1, title="SALES  executive!", company="ACME Limited", location="lagos")
    c = job(3, 2, title="Sales Executive", company="Acme Ltd", location="Abuja")
    merged = merge_jobs([a], [b, c])
    assert [j["id"] for j in merged] == ["t-2", "t-3"]  # newest copy kept, other city kept


def test_same_id_new_version_wins():
    old = job(1, 3, title="Old")
    new = job(1, 3, title="New")
    assert merge_jobs([old], [new])[0]["title"] == "New"


def test_prune_old_expired_and_cap():
    jobs = [
        job(1, 1),
        job(2, 24 * 31),                     # older than 30 days
        job(3, 2, deadline="2026-11-20"),    # past deadline
        job(4, 3, deadline="2026-11-24"),    # deadline today -> kept
    ]
    kept = prune_jobs(jobs, NOW, max_age_days=30, max_total=1000)
    assert [j["id"] for j in kept] == ["t-1", "t-4"]
    many = [job(i, i / 100) for i in range(1, 1201)]  # all within 30 days
    capped = prune_jobs(many, NOW, max_total=1000)
    assert len(capped) == 1000 and capped[0]["id"] == "t-1"


def test_pagination_next_links_and_stale_page_cleanup(tmp_path):
    jobs = [job(i, i) for i in range(1, 651)]
    write_pages(tmp_path, jobs, NOW, page_size=300)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    p2 = json.loads((tmp_path / "page-2.json").read_text(encoding="utf-8"))
    p3 = json.loads((tmp_path / "page-3.json").read_text(encoding="utf-8"))
    assert (latest["count"], latest["next"]) == (300, "page-2.json")
    assert (p2["count"], p2["next"]) == (300, "page-3.json")
    assert (p3["count"], p3["next"]) == (50, None)
    assert list(latest) == ["updated_at", "count", "next", "jobs"]
    assert latest["jobs"][0]["id"] == "t-1"
    assert len(load_jobs(tmp_path)) == 650

    # Shrinks to one page: page-2/3 are deleted, latest has no next
    result = write_pages(tmp_path, jobs[:10], NOW, page_size=300)
    assert result["page-2.json"] == "deleted" and result["page-3.json"] == "deleted"
    assert not (tmp_path / "page-2.json").exists()
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["next"] is None


def test_unchanged_data_gives_identical_files(tmp_path):
    jobs = [job(i, i) for i in range(1, 5)]
    write_pages(tmp_path, jobs, NOW)
    before = (tmp_path / "latest.json").read_bytes()
    res = write_pages(tmp_path, jobs, NOW + timedelta(hours=1))  # later run, same data
    assert res == {"latest.json": "unchanged"}
    assert (tmp_path / "latest.json").read_bytes() == before


def test_utf8_naira_sign_kept(tmp_path):
    write_pages(tmp_path, [job(1)], NOW)
    raw = (tmp_path / "latest.json").read_bytes()
    assert "₦100,000".encode("utf-8") in raw and b"\\u20a6" not in raw


def test_empty_output_still_valid(tmp_path):
    write_pages(tmp_path, [], NOW)
    data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert data["count"] == 0 and data["jobs"] == [] and data["next"] is None


def test_seen_keeps_last_60_days_and_is_stable(tmp_path):
    seen = {"old-1": "2026-08-01", "recent-1": "2026-11-01"}
    seen = update_seen(seen, {"new-1", "recent-1"}, NOW, retention_days=60)
    assert seen == {"recent-1": "2026-11-01", "new-1": "2026-11-24"}
    path = tmp_path / "seen.json"
    assert write_seen(path, seen) is True
    assert write_seen(path, dict(reversed(list(seen.items())))) is False  # same data -> no change
    assert load_seen(path) == seen
