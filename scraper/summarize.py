"""Short summaries in our own words: Gemini if GEMINI_API_KEY is set, else a template."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from difflib import SequenceMatcher

import requests

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-flash-latest"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_SUMMARY_CHARS = 320

PROMPT = """You help Nigerian job seekers. Read the job advert below and reply with JSON only:
{{
 "summary": "1-2 sentences, max 45 words, IN YOUR OWN WORDS (do not copy sentences). Say what the role is, who is hiring, where, and the key requirement.",
 "title": "clean job title, Title Case, no company name, no 'Vacancy'/'Urgent'",
 "company": "employer name, or null if not stated",
 "location": "City, State in Nigeria, or 'Remote', or null",
 "job_type": "Full-time | Part-time | Contract | Internship | Remote",
 "experience": "e.g. '2+ years', 'Entry level', or null",
 "salary": "e.g. '₦150,000 per month', or null if not stated",
 "how_to_apply": "one short sentence, e.g. 'Send your CV to hr@example.com', or null",
 "deadline": "YYYY-MM-DD or null"
}}
Do not invent facts that are not in the advert.

Title: {title}
Advert:
{text}
"""


def copies_source(summary: str, source_text: str, max_run: int = 60) -> bool:
    """True if the summary contains a long verbatim run from the source text."""
    a, b = summary.lower(), (source_text or "").lower()
    if not a or not b:
        return False
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size >= max_run


def shorten(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


_ROLE_PREFIX = re.compile(
    r"^\W*((urgent(ly)?|now|immediate)\s+)*(hiring|recruiting|recruitment|job\s+vacanc(y|ies)|vacanc(y|ies)|"
    r"employment|job\s+opening|job|opening)s?\b\s*(for\s+(an?\s+)?)?[-:!|–]*\s*",
    re.I,
)


def role_from_title(title: str) -> str:
    """'URGENT HIRING - Accountant Needed In Lagos' -> 'Accountant' (for summaries only)."""
    role = _ROLE_PREFIX.sub("", title or "").strip(" -:!")
    role = re.sub(r"\s+(is\s+)?(urgently\s+)?(needed|wanted|required)\b.*$", "", role, flags=re.I)
    role = re.sub(r"\s+(in|at)\s+[A-Z][\w ,/-]*$", "", role).strip(" -:!,")
    return role if len(role) >= 3 else (title or "").strip()


def template_summary(job: dict) -> str:
    """Build a short summary purely from extracted fields (never from the raw text)."""
    title = role_from_title(job.get("title") or "") or "a role"
    company = job.get("company")
    if company == "Not stated":
        company = None
    loc = job.get("location") or "Nigeria"
    who = company if company else "An employer"
    where = "remotely" if loc == "Remote" else f"in {loc}"
    article = "an" if title[:1].lower() in "aeio" else "a"
    s = f"{who} is hiring {article} {title} {where}."
    bits = []
    if job.get("job_type") and job["job_type"] != "Full-time":
        bits.append(job["job_type"].lower())
    exp = job.get("experience")
    if exp and exp != "Not specified":
        bits.append("entry-level" if exp == "Entry level" else f"{exp} experience required")
    if job.get("salary"):
        bits.append(f"pay: {job['salary']}")
    if job.get("deadline"):
        bits.append(f"apply by {job['deadline']}")
    if bits:
        s += " " + "; ".join(bits).capitalize() + "."
    return shorten(s)


class Summarizer:
    def __init__(self, max_calls: int = 60, min_interval: float = 4.5, session=None, sleep=time.sleep):
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
        self.max_calls = max_calls
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self._sleep = sleep
        self._last = 0.0
        self.calls = 0
        self.failures = 0
        self.disabled_reason: str | None = None if self.api_key else "no GEMINI_API_KEY"

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None and self.calls < self.max_calls

    def ai_fields(self, title: str, text: str) -> dict | None:
        """Ask Gemini for a summary + cleaned fields. Returns None on any problem."""
        if not self.enabled:
            return None
        wait = self.min_interval - (time.monotonic() - self._last)
        if self._last and wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        body = {
            "contents": [{"parts": [{"text": PROMPT.format(title=title, text=text[:6000])}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2, "maxOutputTokens": 2048},
        }
        try:
            resp = self.session.post(
                API_URL.format(model=self.model),
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=body,
                timeout=40,
            )
            if resp.status_code in (401, 403, 404):
                self.disabled_reason = f"Gemini HTTP {resp.status_code} (bad key or model '{self.model}')"
                log.warning(self.disabled_reason + "; using built-in summaries for the rest of this run")
                return None
            if resp.status_code == 429:
                self.disabled_reason = "Gemini rate limit reached"
                log.warning(self.disabled_reason + "; using built-in summaries for the rest of this run")
                return None
            resp.raise_for_status()
            parts = resp.json()["candidates"][0]["content"]["parts"]
            raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except Exception as exc:
            self.failures += 1
            log.warning("Gemini call failed: %s", str(exc).replace(self.api_key, "***"))
            if self.failures >= 5:
                self.disabled_reason = "too many Gemini failures"
            return None
        summary = shorten(str(data.get("summary") or ""))
        if not summary or copies_source(summary, text):
            data["summary"] = None
        else:
            data["summary"] = summary
        return data
