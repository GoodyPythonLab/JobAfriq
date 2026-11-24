"""Run all enabled sources, merge with existing data, write docs/*.json and data/seen.json.

    python -m scraper                              # normal run (writes docs/ and data/)
    python -m scraper --out-dir /tmp/x --data-dir /tmp/x   # dry run somewhere else
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .http import PoliteSession
from .jobs import build_job
from .sources import build_source
from .store import (
    load_jobs, load_seen, merge_jobs, prune_jobs, update_seen, write_pages, write_seen,
)
from .summarize import Summarizer

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("scraper")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m scraper", description="JobAfriq job scraper")
    p.add_argument("--config", default=str(ROOT / "sources.yaml"))
    p.add_argument("--out-dir", default=str(ROOT / "docs"), help="where latest.json etc. are written")
    p.add_argument("--data-dir", default=str(ROOT / "data"), help="where seen.json is kept")
    p.add_argument("--only", nargs="*", help="run only these source names")
    p.add_argument("--no-ai", action="store_true", help="never call Gemini, even if a key is set")
    p.add_argument("--report", help="write a JSON run report to this path")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def run(args) -> dict:
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    cfg = load_config(args.config)
    st = cfg["settings"]
    out_dir, data_dir = Path(args.out_dir), Path(args.data_dir)
    seen_path = data_dir / "seen.json"

    seen = load_seen(seen_path)
    session = PoliteSession(
        st["user_agent"], st["delay_seconds"], st["timeout_seconds"], st["max_retries"]
    )
    summarizer = Summarizer(st["ai"]["max_calls_per_run"], st["ai"]["min_interval_seconds"])
    if args.no_ai:
        summarizer.disabled_reason = "--no-ai"
    log.info("AI summaries: %s", "on (model %s)" % summarizer.model if summarizer.enabled else f"off ({summarizer.disabled_reason})")

    report: dict = {"started_at": now.isoformat(), "sources": {}}
    new_jobs: list[dict] = []
    handled: set[str] = set()

    for src_cfg in cfg["sources"]:
        name = src_cfg["name"]
        if not src_cfg.get("enabled", True) or (args.only and name not in args.only):
            continue
        stats = {"found": 0, "errors": []}
        report["sources"][name] = stats
        try:
            source = build_source(src_cfg)
            label = src_cfg.get("label") or name
            for raw in source.fetch(session, set(seen)):
                try:
                    new_jobs.append(build_job(raw, summarizer, label, now))
                    stats["found"] += 1
                except Exception as exc:  # a bad post never stops the run
                    source.error(f"could not build job {raw.job_id}: {exc!r}")
            handled |= source.handled_ids
            stats["errors"] = source.errors
        except Exception as exc:  # a broken source never stops the run
            log.exception("source %s crashed", name)
            stats["errors"].append(f"source crashed: {exc!r}")
        log.info("%s: %d new jobs, %d errors", name, stats["found"], len(stats["errors"]))

    existing = load_jobs(out_dir)
    merged = merge_jobs(existing, new_jobs)
    final = prune_jobs(merged, now, st["max_age_days"], st["max_jobs_total"])
    files = write_pages(out_dir, final, now, st["page_size"])
    (out_dir / ".nojekyll").touch(exist_ok=True)

    seen = update_seen(seen, handled, now, st["seen_retention_days"])
    seen_changed = write_seen(seen_path, seen)

    report.update(
        {
            "existing_jobs": len(existing),
            "new_jobs": len(new_jobs),
            "total_jobs": len(final),
            "files": files,
            "seen_ids": len(seen),
            "seen_changed": seen_changed,
            "http_requests": session.request_count,
            "ai": {"calls": summarizer.calls, "failures": summarizer.failures, "status": summarizer.disabled_reason or "on"},
            "seconds": round(time.monotonic() - started, 1),
        }
    )
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    report = run(args)
    print("\n=== JobAfriq scrape summary ===")
    for name, s in report["sources"].items():
        print(f"  {name:<18} {s['found']:>4} new jobs   {len(s['errors'])} error(s)")
        for e in s["errors"][:5]:
            print(f"      - {e}")
    print(f"  total jobs now: {report['total_jobs']} (was {report['existing_jobs']}), "
          f"requests: {report['http_requests']}, AI calls: {report['ai']['calls']}, {report['seconds']}s")
    print(f"  files: {report['files']}")
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    ran = report["sources"].values()
    # Exit non-zero (red X on GitHub) only if every source failed and produced nothing.
    # Output files were still written, so existing jobs are pruned correctly either way.
    if ran and all(s["errors"] and not s["found"] for s in ran):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
