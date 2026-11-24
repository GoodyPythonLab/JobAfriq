"""Nairaland Jobs/Vacancies board (https://www.nairaland.com/jobs).

Board pages:  /jobs (page 1), /jobs/1 (page 2), ...
  Each thread row is <td id="top{THREAD_ID}"> ... <b><a href="/{THREAD_ID}/{slug}">Title</a></b>
Thread pages: /{THREAD_ID}/{slug}
  <table summary="posts">: header row <td class="bold l pu"> with <span class="s"> (time),
  then body row <td id="pb{POST_ID}"><div class="narrow">post text</div>.
We read only the first post (the job ad). Selectors have fallbacks in case the layout shifts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..extract import MONTHS, WAT, clean_text, tidy_title
from ..http import BlockedError, FetchError, PoliteSession
from .base import RawPost, Source

THREAD_HREF_RE = re.compile(r"^(?:https?://(?:www\.)?nairaland\.com)?/(\d{5,})/([\w.%-]+)/?$")
TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*([ap]m)(?:\s+On\s+([A-Za-z]{3,9})\s+(\d{1,2})(?:,\s*(\d{4}))?)?", re.I
)


def decode_cfemail(hexstr: str) -> str:
    """Decode Cloudflare's e-mail obfuscation (data-cfemail)."""
    try:
        data = bytes.fromhex(hexstr)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except (ValueError, IndexError, UnicodeDecodeError):
        return ""


def parse_nairaland_time(text: str, now: datetime) -> datetime | None:
    """'10:15am On Nov 23, 2025' / '10:15am On Nov 23' / '10:15am' (today), in Nigerian time."""
    m = TIME_RE.search(text or "")
    if not m:
        return None
    hour, minute, ampm, mon, day, year = m.groups()
    hour = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    local_now = now.astimezone(WAT)
    try:
        if mon:
            month = MONTHS[mon.lower()[:3]]
            y = int(year) if year else local_now.year
            dt = datetime(y, month, int(day), hour, int(minute), tzinfo=WAT)
            if not year and dt > local_now.replace(microsecond=0) and (dt - local_now).days > 1:
                dt = dt.replace(year=y - 1)
        else:
            dt = local_now.replace(hour=hour, minute=int(minute), second=0, microsecond=0)
    except (KeyError, ValueError):
        return None
    return dt.astimezone(timezone.utc)


def parse_board(html: str, base_url: str) -> list[dict]:
    """Return [{'thread_id', 'title', 'url'}] in page order, no duplicates."""
    soup = BeautifulSoup(html, "lxml")
    threads: list[dict] = []
    seen: set[str] = set()

    def add(a) -> None:
        m = THREAD_HREF_RE.match(a.get("href", "").split("#")[0])
        if not m:
            return
        tid = m.group(1)
        title = a.get_text(" ", strip=True)
        if tid in seen or not title or title.startswith("("):
            return
        seen.add(tid)
        threads.append({"thread_id": tid, "title": tidy_title(title), "url": urljoin(base_url, f"/{tid}/{m.group(2)}")})

    rows = soup.select('td[id^="top"]')
    for td in rows:
        a = td.select_one("b > a[href]") or td.find("a", href=THREAD_HREF_RE)
        if a:
            add(a)
    if not threads:  # fallback: any thread-looking link inside the main topic table
        container = soup.find("table", attrs={"summary": re.compile("links|topics", re.I)}) or soup
        for a in container.find_all("a", href=True):
            add(a)
    return threads


def _post_text(body) -> tuple[str, list[str]]:
    for q in body.find_all("blockquote"):
        q.decompose()  # quoted replies are not part of the ad
    for el in body.select("[data-cfemail]"):
        el.replace_with(decode_cfemail(el["data-cfemail"]))
    for a in body.select('a[href*="/cdn-cgi/l/email-protection#"]'):
        a.replace_with(decode_cfemail(a["href"].split("#", 1)[1]))
    links = []
    for a in body.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "nairaland.com" not in href:
            links.append(href)
    for br in body.find_all("br"):
        br.replace_with("\n")
    return clean_text(body.get_text()), links


def parse_thread(html: str, now: datetime) -> dict:
    """Parse the first post of a thread page -> {'title', 'text', 'posted_at', 'links', 'post_id'}."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", attrs={"summary": "posts"}) or soup

    body_td = table.select_one('td[id^="pb"]')
    body = None
    if body_td is not None:
        body = body_td.select_one("div.narrow") or body_td
    if body is None:
        body = table.select_one("div.narrow")
    if body is None:
        raise ValueError("first post body not found (layout changed?)")
    post_id = body_td["id"][2:] if body_td is not None and body_td.get("id", "").startswith("pb") else None

    header = table.select_one("td.pu") or (body_td.find_parent("tr").find_previous_sibling("tr") if body_td else None)
    posted_at = None
    if header is not None:
        span = header.select_one("span.s") or header
        posted_at = parse_nairaland_time(span.get_text(" ", strip=True), now)

    title = None
    h2 = soup.find("h2")
    if h2:
        title = re.sub(r"\s+-\s+(Jobs/Vacancies|Jobs|Career)\b.*$", "", h2.get_text(" ", strip=True), flags=re.I)
        title = re.sub(r"\s+-\s+Nairaland\s*$", "", title)
    text, links = _post_text(body)
    return {"title": tidy_title(title) if title else None, "text": text, "posted_at": posted_at, "links": links, "post_id": post_id}


class NairalandSource(Source):
    def fetch(self, session: PoliteSession, seen: set[str]) -> Iterator[RawPost]:
        base = self.config.get("base_url", "https://www.nairaland.com").rstrip("/")
        board = self.config.get("board", "jobs").strip("/")
        pages = int(self.config.get("pages", 2))
        cap = int(self.config.get("max_threads", 40))
        skip_res = [re.compile(p, re.I) for p in self.config.get("skip_title_patterns", [])]

        threads: list[dict] = []
        for page in range(pages):
            url = f"{base}/{board}" + (f"/{page}" if page else "")
            try:
                resp = session.get(url)
            except BlockedError as exc:
                self.error(str(exc))
                return  # no point continuing: every page will be blocked
            except FetchError as exc:
                self.error(f"board page {url}: {exc}")
                continue
            found = parse_board(resp.text, base)
            if not found:
                self.error(f"no threads found on {url} (layout changed?)")
            threads.extend(t for t in found if t["thread_id"] not in {x["thread_id"] for x in threads})

        # The board is sorted by last reply, so old threads get bumped up. Thread IDs grow over
        # time, so anything far below the newest ID is old: skip it without opening it.
        max_gap = int(self.config.get("max_id_gap", 20000))
        newest = max((int(t["thread_id"]) for t in threads), default=0)
        new = [
            t for t in threads
            if self.make_id(t["thread_id"]) not in seen and newest - int(t["thread_id"]) <= max_gap
        ]
        self.log.info("%d threads on board, %d new, fetching up to %d", len(threads), len(new), cap)

        for t in new:
            job_id = self.make_id(t["thread_id"])
            if any(rx.search(t["title"]) for rx in skip_res):
                self.handled_ids.add(job_id)
                continue
            if cap <= 0:
                break
            cap -= 1
            try:
                resp = session.get(t["url"])
            except BlockedError as exc:
                self.error(str(exc))
                return
            except FetchError as exc:
                self.error(f"thread {t['url']}: {exc}")  # not marked handled -> retried next run
                continue
            try:
                post = parse_thread(resp.text, datetime.now(timezone.utc))
            except Exception as exc:  # one bad post must never stop the run
                self.error(f"could not parse {t['url']}: {exc}")
                self.handled_ids.add(job_id)
                continue
            self.handled_ids.add(job_id)
            if len(post["text"]) < 40:
                continue  # empty / image-only post, nothing to extract
            yield RawPost(
                job_id=job_id,
                title=post["title"] or t["title"],
                text=post["text"],
                url=t["url"],
                source=self.name,
                posted_at=post["posted_at"],
                links=post["links"],
            )
