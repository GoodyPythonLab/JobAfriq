"""Polite HTTP client: robots.txt, delays, timeouts, limited retries, block detection."""

from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)


class FetchError(Exception):
    """A request failed after retries (network error, bad status...)."""


class BlockedError(FetchError):
    """The site served a bot challenge / refused us. Retrying will not help."""


class DisallowedError(FetchError):
    """robots.txt does not allow this path."""


# --------------------------------------------------------------------------- robots.txt


class RobotsRules:
    """Minimal robots.txt matcher with `*` / `$` wildcards and longest-match precedence.

    (The stdlib parser ignores wildcards, which several Nigerian job sites use.)
    """

    def __init__(self, text: str, user_agent: str):
        self.rules: list[tuple[bool, re.Pattern, int]] = []
        groups = self._parse_groups(text)
        token = user_agent.split("/")[0].strip().lower()
        chosen = None
        for agents, rules in groups:
            if any(a != "*" and a in token for a in agents):
                chosen = rules
                break
        if chosen is None:
            for agents, rules in groups:
                if "*" in agents:
                    chosen = rules
                    break
        for allow, path in chosen or []:
            if not path:
                continue  # "Disallow:" (empty) means allow everything
            self.rules.append((allow, self._compile(path), len(path)))

    @staticmethod
    def _parse_groups(text: str):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent and (agents or rules):
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(val.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                rules.append((key == "allow", val))
                last_was_agent = False
            else:
                last_was_agent = False
        if agents:
            groups.append((agents, rules))
        return groups

    @staticmethod
    def _compile(path: str) -> re.Pattern:
        anchored = path.endswith("$")
        if anchored:
            path = path[:-1]
        pattern = ".*".join(re.escape(part) for part in path.split("*"))
        return re.compile(pattern + ("$" if anchored else ""))

    def allowed(self, path: str) -> bool:
        best: tuple[int, bool] | None = None
        for allow, rx, length in self.rules:
            if rx.match(path):
                # Longest rule wins; on a tie, Allow wins.
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]


def looks_like_challenge(resp: requests.Response) -> bool:
    if resp.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if resp.status_code in (403, 429, 503):
        head = resp.text[:3000].lower()
        return "just a moment" in head or "challenge-platform" in head or "attention required" in head
    return False


# --------------------------------------------------------------------------- client


class PoliteSession:
    def __init__(
        self,
        user_agent: str,
        delay_seconds=(1.5, 2.0),
        timeout: float = 20,
        max_retries: int = 2,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ):
        self.user_agent = user_agent
        self.delay = (float(delay_seconds[0]), float(delay_seconds[-1]))
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en-NG,en;q=0.9",
            }
        )
        self._sleep = sleep
        self._last_request = 0.0
        self._robots: dict[str, RobotsRules] = {}
        self.request_count = 0

    def _wait(self) -> None:
        if self._last_request:
            wait = random.uniform(*self.delay) - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = time.monotonic()

    def _raw_get(self, url: str) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait()
            self.request_count += 1
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
                continue
            if looks_like_challenge(resp):
                raise BlockedError(
                    f"{urlsplit(url).netloc} served a bot challenge (HTTP {resp.status_code}); "
                    "the site is blocking automated access from this network"
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                log.warning("GET %s -> HTTP %d (attempt %d)", url, resp.status_code, attempt + 1)
                self._sleep(2 * (attempt + 1))
                continue
            return resp
        raise FetchError(f"Giving up on {url}: {last_exc}")

    def robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            try:
                resp = self._raw_get(origin + "/robots.txt")
                if resp.status_code == 200:
                    rules = RobotsRules(resp.text, self.user_agent)
                elif resp.status_code in (401, 403):
                    rules = RobotsRules("User-agent: *\nDisallow: /", self.user_agent)
                else:  # 404 etc.: no restrictions
                    rules = RobotsRules("", self.user_agent)
            except BlockedError:
                raise
            except FetchError as exc:
                # Can't read robots.txt -> be conservative and skip the site this run.
                raise FetchError(f"Could not read robots.txt for {origin}: {exc}") from exc
            self._robots[origin] = rules
        return self._robots[origin]

    def get(self, url: str) -> requests.Response:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if not self.robots_for(url).allowed(path):
            raise DisallowedError(f"robots.txt disallows {url}")
        resp = self._raw_get(url)
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        return resp
