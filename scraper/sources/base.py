"""Common types for job sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from ..http import PoliteSession


@dataclass
class RawPost:
    """One job ad as read from a source, before field extraction."""

    job_id: str                      # e.g. "nl-8123456"
    title: str
    text: str                        # plain text of the ad (only used in memory, never output)
    url: str
    source: str
    posted_at: datetime | None = None
    company_hint: str | None = None
    category_hint: str | None = None
    trusted: bool = False            # curated job board (contact checks relaxed)
    links: list[str] = field(default_factory=list)  # outbound links found in the ad


class Source:
    """Subclass and implement `fetch`. Config comes straight from sources.yaml."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.name: str = config["name"]
        self.prefix: str = config["prefix"]
        self.log = logging.getLogger(f"scraper.sources.{self.name}")
        self.errors: list[str] = []
        # IDs handled this run that should not be fetched again (incl. skipped / unparseable)
        self.handled_ids: set[str] = set()

    def make_id(self, native_id: str) -> str:
        return f"{self.prefix}-{native_id}"

    def error(self, msg: str) -> None:
        self.log.warning(msg)
        self.errors.append(msg)

    def fetch(self, session: PoliteSession, seen: set[str]) -> Iterator[RawPost]:
        raise NotImplementedError
