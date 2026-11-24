import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return fixture_bytes(name).decode("utf-8")


def make_response(status: int = 200, body: bytes | str = b"", headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode("utf-8") if isinstance(body, str) else body
    r.headers.update(headers or {})
    r.encoding = "utf-8"
    return r


class FakeHTTP:
    """Stands in for requests.Session: maps URL -> Response (or exception, or list of them)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.headers: dict = {}
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        resp = self.routes.get(url)
        if resp is None:
            return make_response(404, "not found")
        if isinstance(resp, list):
            resp = resp.pop(0) if len(resp) > 1 else resp[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture
def no_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
