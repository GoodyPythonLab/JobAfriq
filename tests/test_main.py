import json
from argparse import Namespace

import scraper.main as main_mod
from conftest import FakeHTTP, fixture_bytes, fixture_text, make_response
from scraper.http import PoliteSession

ROUTES = {
    "https://www.nairaland.com/robots.txt": make_response(200, "User-agent: *\nDisallow: /login\n"),
    "https://www.nairaland.com/jobs": make_response(403, fixture_text("cloudflare_challenge.html"), {"cf-mitigated": "challenge"}),
    "https://www.hotnigerianjobs.com/robots.txt": make_response(200, "User-agent: *\nAllow: /\n"),
    "https://www.hotnigerianjobs.com/feed/rss.xml": make_response(200, fixture_bytes("hotnigerianjobs_feed.xml")),
}

CONFIG = """
settings: {delay_seconds: [0, 0]}
sources:
  - {name: nairaland, type: nairaland, prefix: nl, base_url: "https://www.nairaland.com", pages: 2}
  - {name: broken, type: does-not-exist, prefix: br}
  - {name: hotnigerianjobs, type: rss, prefix: hnj, label: HotNigerianJobs,
     url: "https://www.hotnigerianjobs.com/feed/rss.xml", id_pattern: "/hotjobs/(\\\\d+)/"}
  - {name: disabled-feed, type: rss, enabled: false, prefix: dis, url: "https://off.example/feed"}
"""


def test_full_run_survives_failing_sources(tmp_path, monkeypatch, no_gemini):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(CONFIG)
    monkeypatch.setattr(
        main_mod, "PoliteSession",
        lambda ua, *a, **k: PoliteSession(ua, session=FakeHTTP(ROUTES), sleep=lambda _: None),
    )
    args = Namespace(config=str(cfg), out_dir=str(tmp_path / "docs"), data_dir=str(tmp_path / "data"),
                     only=None, no_ai=False, report=None, verbose=False)
    report = main_mod.run(args)

    assert report["sources"]["nairaland"]["found"] == 0
    assert "bot challenge" in report["sources"]["nairaland"]["errors"][0]
    assert "crashed" in report["sources"]["broken"]["errors"][0]
    assert "disabled-feed" not in report["sources"]
    assert report["sources"]["hotnigerianjobs"]["found"] == 5

    latest = json.loads((tmp_path / "docs" / "latest.json").read_text(encoding="utf-8"))
    assert latest["count"] == 5 and latest["next"] is None
    assert (tmp_path / "docs" / ".nojekyll").exists()
    seen = json.loads((tmp_path / "data" / "seen.json").read_text(encoding="utf-8"))
    assert len(seen) == 5 and all(k.startswith("hnj-") for k in seen)

    # Second run: nothing new -> files untouched
    before = (tmp_path / "docs" / "latest.json").read_bytes()
    report2 = main_mod.run(args)
    assert report2["new_jobs"] == 0 and report2["files"]["latest.json"] == "unchanged"
    assert report2["seen_changed"] is False
    assert (tmp_path / "docs" / "latest.json").read_bytes() == before
