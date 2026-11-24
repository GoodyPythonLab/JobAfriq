"""Load sources.yaml and fill in defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_SETTINGS: dict[str, Any] = {
    "user_agent": "JobAfriqBot/1.0 (free job-alert app for Nigerian job seekers)",
    "delay_seconds": [1.5, 2.0],
    "timeout_seconds": 20,
    "max_retries": 2,
    "max_jobs_total": 1000,
    "page_size": 300,
    "max_age_days": 30,
    "seen_retention_days": 60,
    "ai": {"max_calls_per_run": 60, "min_interval_seconds": 4.5},
}


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}
    settings["ai"] = {**DEFAULT_SETTINGS["ai"], **(settings.get("ai") or {})}
    sources = []
    for src in raw.get("sources") or []:
        if not isinstance(src, dict) or not isinstance(src.get("name"), str) or not src.get("type"):
            raise ValueError(
                f"Each source needs a text 'name' and a 'type' (quote values like \"on\"/\"off\"/\"no\"): {src!r}"
            )
        src.setdefault("enabled", True)
        src.setdefault("prefix", src["name"][:3])
        sources.append(src)
    return {"settings": settings, "sources": sources}
