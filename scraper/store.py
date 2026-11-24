"""Load/merge/prune jobs and write the paginated JSON files + seen.json."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .extract import WAT, normalise_key

log = logging.getLogger(__name__)

PAGE_RE = re.compile(r"^page-(\d+)\.json$")


def page_name(index: int) -> str:
    """index 0 -> latest.json, 1 -> page-2.json, ..."""
    return "latest.json" if index == 0 else f"page-{index + 1}.json"


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- loading


def load_jobs(out_dir: Path) -> list[dict]:
    jobs: list[dict] = []
    files = []
    if (out_dir / "latest.json").exists():
        files.append(out_dir / "latest.json")
    files += sorted(
        (p for p in out_dir.glob("page-*.json") if PAGE_RE.match(p.name)),
        key=lambda p: int(PAGE_RE.match(p.name).group(1)),
    )
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            jobs.extend(j for j in data.get("jobs", []) if isinstance(j, dict) and j.get("id"))
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s: %s (ignoring it)", f, exc)
    return jobs


# --------------------------------------------------------------------------- merge / prune


def dedupe_key(job: dict) -> str:
    return normalise_key(job.get("title"), job.get("company"), job.get("location"))


def merge_jobs(existing: list[dict], new: list[dict]) -> list[dict]:
    """Combine, newest first; one job per ID and per normalised title+company+location."""
    by_id: dict[str, dict] = {}
    for job in existing + new:  # new wins on equal IDs
        by_id[job["id"]] = job
    ordered = sort_jobs(list(by_id.values()))
    out, keys = [], set()
    for job in ordered:
        k = dedupe_key(job)
        if k in keys:
            continue
        keys.add(k)
        out.append(job)
    return out


def sort_jobs(jobs: list[dict]) -> list[dict]:
    # Newest first; ID as a tie-breaker so the order is stable between runs.
    return sorted(jobs, key=lambda j: (j.get("posted_at") or "", j["id"]), reverse=True)


def prune_jobs(jobs: list[dict], now: datetime, max_age_days: int = 30, max_total: int = 1000) -> list[dict]:
    cutoff = now - timedelta(days=max_age_days)
    today = now.astimezone(WAT).date().isoformat()
    kept = []
    for job in jobs:
        posted = _parse_ts(job.get("posted_at"))
        if posted is None or posted < cutoff:
            continue
        if job.get("deadline") and job["deadline"] < today:
            continue
        kept.append(job)
    return sort_jobs(kept)[:max_total]


# --------------------------------------------------------------------------- writing


def render_page(jobs: list[dict], updated_at: str, next_page: str | None) -> str:
    """Valid JSON with one job per line: small, and gives readable git diffs."""
    head = json.dumps({"updated_at": updated_at, "count": len(jobs), "next": next_page}, ensure_ascii=False)
    lines = [json.dumps(j, ensure_ascii=False, separators=(",", ":")) for j in jobs]
    body = ",\n".join(lines)
    return head[:-1] + ', "jobs": [\n' + body + ("\n" if lines else "") + "]}\n"


def write_pages(out_dir: Path, jobs: list[dict], now: datetime, page_size: int = 300) -> dict[str, str]:
    """Write latest.json + page-N.json. Files whose jobs didn't change are left untouched.

    Returns {filename: "written" | "unchanged" | "deleted"}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    now_iso = now.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    chunks = [jobs[i: i + page_size] for i in range(0, len(jobs), page_size)] or [[]]
    result: dict[str, str] = {}
    for idx, chunk in enumerate(chunks):
        name = page_name(idx)
        nxt = page_name(idx + 1) if idx + 1 < len(chunks) else None
        path = out_dir / name
        updated_at = now_iso
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                if old.get("jobs") == chunk and old.get("next") == nxt:
                    result[name] = "unchanged"
                    continue
            except ValueError:
                pass
        path.write_text(render_page(chunk, updated_at, nxt), encoding="utf-8")
        result[name] = "written"
    wanted = {page_name(i) for i in range(len(chunks))}
    for p in out_dir.glob("page-*.json"):
        if PAGE_RE.match(p.name) and p.name not in wanted:
            p.unlink()
            result[p.name] = "deleted"
    return result


# --------------------------------------------------------------------------- seen.json


def load_seen(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        if isinstance(data, list):  # tolerate a plain list of IDs
            return {str(k): date.today().isoformat() for k in data}
    except (OSError, ValueError):
        pass
    return {}


def update_seen(seen: dict[str, str], new_ids, now: datetime, retention_days: int = 60) -> dict[str, str]:
    today = now.astimezone(WAT).date()
    for i in new_ids:
        seen.setdefault(i, today.isoformat())
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


def write_seen(path: Path, seen: dict[str, str]) -> bool:
    """Write {id: first_seen_date} sorted by ID. Returns True if the file changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(sorted(seen.items())), ensure_ascii=False, indent=0) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True
