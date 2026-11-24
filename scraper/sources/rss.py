"""Generic RSS 2.0 job feed source (configured entirely from sources.yaml)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

from bs4 import BeautifulSoup
from lxml import etree

from ..extract import clean_text, fix_mojibake, tidy_title
from ..http import FetchError, PoliteSession
from .base import RawPost, Source

_XML_DECL_RE = re.compile(rb"^\s*<\?xml[^>]*\?>")


def decode_feed(data: bytes) -> bytes:
    """Return UTF-8 bytes without an XML declaration.

    Some feeds declare iso-8859-1 but actually send UTF-8 (e.g. MyJobMag), which would
    turn "we’re" into "weâ€™re". Prefer UTF-8 whenever the bytes are valid UTF-8.
    """
    decl = _XML_DECL_RE.match(data)
    body = data[decl.end():] if decl else data
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        enc = "utf-8"
        if decl:
            m = re.search(rb"encoding=[\"']([\w.-]+)", decl.group(0))
            if m:
                enc = m.group(1).decode()
        text = body.decode(enc, errors="replace")
    return text.encode("utf-8")


def _html_to_text(fragment: str) -> str:
    if "<" not in fragment:
        return clean_text(fragment)
    soup = BeautifulSoup(fragment, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all(["p", "li", "div"]):
        p.insert_after("\n")
    return clean_text(soup.get_text())


def parse_rss(data: bytes) -> list[dict]:
    """Parse RSS bytes into dicts: title, link, guid, description, pub_date, categories, extra."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=False)
    root = etree.fromstring(decode_feed(data), parser)
    if root is None:
        raise ValueError("feed is not valid XML")
    items = []
    for it in root.iter("item"):
        def txt(tag: str) -> str:
            el = it.find(tag)
            return fix_mojibake(el.text.strip()) if el is not None and el.text else ""

        pub = None
        if txt("pubDate"):
            try:
                pub = parsedate_to_datetime(txt("pubDate")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                pub = None
        extra = {
            child.tag: (child.text or "").strip()
            for child in it
            if isinstance(child.tag, str)
            and child.tag not in ("title", "link", "guid", "description", "pubDate", "category")
            and child.text and child.text.strip()
        }
        items.append(
            {
                "title": txt("title"),
                "link": txt("link"),
                "guid": txt("guid") or txt("link"),
                "description": _html_to_text(txt("description")),
                "pub_date": pub,
                "categories": [c.text.strip() for c in it.findall("category") if c.text and c.text.strip()],
                "extra": extra,
            }
        )
    return items


def split_title_company(title: str) -> tuple[str, str | None]:
    """'Accountant at Dangote Group - 3 Openings' -> ('Accountant', 'Dangote Group')."""
    title = re.sub(r"\s*[-–(]\s*\d+\s+openings?\)?\s*$", "", title.strip(), flags=re.I)
    m = re.match(r"^(.*\S)\s+at\s+(\S.*)$", title)
    if m:
        role, company = m.group(1).strip(), m.group(2).strip()
        if GENERIC_ROLE_RE.match(role):  # "Jobs at Carlson Hotel": keep the whole title
            return title, company
        return role, company
    return title, None


GENERIC_ROLE_RE = re.compile(
    r"^(latest\s+)?(jobs?|vacanc(y|ies)|job\s+vacanc(y|ies)|job\s+openings?|openings?|careers?|"
    r"career\s+opportunities|recruitment|positions?)$",
    re.I,
)


class RssSource(Source):
    def native_id(self, item: dict) -> str:
        pattern = self.config.get("id_pattern")
        key = item["link"] or item["guid"]
        if pattern:
            m = re.search(pattern, key) or re.search(pattern, item["guid"])
            if m:
                return m.group(1)
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]

    def fetch(self, session: PoliteSession, seen: set[str]) -> Iterator[RawPost]:
        url = self.config["url"]
        try:
            resp = session.get(url)
            items = parse_rss(resp.content)
        except FetchError as exc:
            self.error(str(exc))
            return
        except Exception as exc:
            self.error(f"could not parse feed {url}: {exc}")
            return
        if not items:
            self.error(f"feed {url} had no items")
            return
        cap = int(self.config.get("max_items", 100))
        for item in items[:cap]:
            try:
                if not item["title"] or not item["link"]:
                    continue
                job_id = self.make_id(self.native_id(item))
                if job_id in seen:
                    continue
                self.handled_ids.add(job_id)
                title, company = split_title_company(item["title"])
                hint = item["extra"].get("industry") or (item["categories"][0] if item["categories"] else None)
                yield RawPost(
                    job_id=job_id,
                    title=tidy_title(title),
                    text=item["description"],
                    url=item["link"],
                    source=self.name,
                    posted_at=item["pub_date"] or datetime.now(timezone.utc),
                    company_hint=company,
                    category_hint=hint,
                    trusted=True,
                )
            except Exception as exc:
                self.error(f"bad item in {url}: {exc}")
