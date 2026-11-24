"""Turn a RawPost into the JSON job record served to the app."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from . import extract as ex
from .redflags import detect_red_flags
from .sources.base import RawPost
from .summarize import Summarizer, shorten, template_summary

FIELDS = [
    "id", "title", "company", "location", "category", "job_type", "experience", "salary",
    "summary", "how_to_apply", "deadline", "posted_at", "source", "source_url", "red_flags",
]
JOB_TYPES = {"Full-time", "Part-time", "Contract", "Internship", "Remote"}


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _how_to_apply(raw: RawPost, emails: list[str], phones: list[str], links: list[str], label: str) -> str:
    if raw.trusted:
        return f"Open the full ad on {label} for application details."
    parts = []
    if emails:
        parts.append("Send your CV to " + " or ".join(emails[:2]))
    if links:
        parts.append("Apply online: " + links[0])
    if phones and not emails:
        parts.append("Call " + ", ".join(phones[:2]))
    if not parts:
        return f"See the original post on {label}."
    return shorten("; ".join(parts) + ".", 240)


def _valid_date(s) -> str | None:
    if isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            date.fromisoformat(s)
            return s
        except ValueError:
            return None
    return None


def build_job(raw: RawPost, summarizer: Summarizer | None, label: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    text = raw.text or ""
    today = now.astimezone(ex.WAT).date()

    emails = ex.extract_emails(text)
    phones = ex.extract_phones(text)
    links = [u for u in dict.fromkeys(raw.links + ex.extract_urls(text)) if "nairaland.com" not in u]
    title = raw.title
    company = raw.company_hint or ex.extract_company(title, text)
    location = ex.detect_location(title, text)
    job_type = ex.detect_job_type(title, text)
    experience = ex.detect_experience(text)
    salary, salary_monthly = ex.extract_salary(text)
    deadline = ex.extract_deadline(text, today)
    how_to_apply = _how_to_apply(raw, emails, phones, links, label)
    summary = None

    ai = summarizer.ai_fields(title, text) if summarizer else None
    if ai:
        summary = ai.get("summary")
        t = ai.get("title")
        if isinstance(t, str) and 3 <= len(t) <= 120:
            title = ex.tidy_title(t)
        c = ai.get("company")
        if not raw.company_hint and isinstance(c, str) and 2 < len(c) <= 80 and c.lower() not in ("null", "none", "not stated"):
            company = c.strip()
        loc = ai.get("location")
        if isinstance(loc, str) and loc.strip():
            ai_loc = ex.detect_location(loc) if loc.strip().lower() != "remote" else "Remote"
            if ai_loc != "Nigeria":
                location = ai_loc
        if ai.get("job_type") in JOB_TYPES:
            job_type = ai["job_type"]
        e = ai.get("experience")
        if experience == "Not specified" and isinstance(e, str) and 0 < len(e) <= 30:
            experience = e.strip()
        s = ai.get("salary")
        if not salary and isinstance(s, str) and re.search(r"\d", s) and len(s) <= 60:
            salary = s.strip()
            _, salary_monthly = ex.extract_salary("salary: " + salary)
        h = ai.get("how_to_apply")
        if not raw.trusted and isinstance(h, str) and 5 < len(h) <= 240:
            how_to_apply = h.strip()
        deadline = deadline or _valid_date(ai.get("deadline"))

    category = ex.categorize(title, text, raw.category_hint)
    job = {
        "id": raw.job_id,
        "title": title,
        "company": company or "Not stated",
        "location": location,
        "category": category,
        "job_type": job_type,
        "experience": experience,
        "salary": salary,
        "summary": None,
        "how_to_apply": how_to_apply,
        "deadline": deadline,
        "posted_at": iso_utc(raw.posted_at or now),
        "source": raw.source,
        "source_url": raw.url,
        "red_flags": detect_red_flags(
            text=text,
            company=company,
            salary_monthly=salary_monthly,
            has_official_link=bool(links),
            trusted_source=raw.trusted,
        ),
    }
    job["summary"] = summary or template_summary(job)
    return job
