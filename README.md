# JobAfriq – job feed backend

This repository produces the job listings used by the **JobAfriq** Android app, a free job-alert app for Nigerian job seekers.

Every hour a GitHub Actions workflow:

1. reads new job posts from the sources listed in [`sources.yaml`](sources.yaml):
   - the **Nairaland Jobs/Vacancies** board
   - the **MyJobMag** RSS feed
   - the **HotNigerianJobs** RSS feed
2. pulls out the useful fields: title, company, location, category, salary, deadline, contacts and so on;
3. writes a short summary in its own words, using Google Gemini if you add an API key and a built-in template otherwise;
4. adds **red-flag warnings** for likely scams, such as fees, WhatsApp-only contact or visa offers;
5. publishes everything as JSON files in [`docs/`](docs/), which GitHub Pages serves to the app.

The app only needs to download `latest.json`. Older jobs are on `page-2.json`, `page-3.json` and so on, linked through the `next` field.

## Output format

Each file looks like this:

```json
{"updated_at": "2026-11-24T17:00:00Z", "count": 300, "next": "page-2.json", "jobs": [
{"id":"nl-8123456","title":"Accountant","company":"Greenfield Foods Limited","location":"Ikeja, Lagos",
 "category":"Accounting","job_type":"Full-time","experience":"3+ years","salary":"₦250,000 - ₦300,000 per month",
 "summary":"Greenfield Foods Limited is hiring an Accountant in Ikeja, Lagos. ...",
 "how_to_apply":"Send your CV to careers@example.com.","deadline":"2026-11-30",
 "posted_at":"2026-11-23T09:15:00Z","source":"nairaland",
 "source_url":"https://www.nairaland.com/8123456/...","red_flags":[]}
]}
```

| Field | Notes |
|---|---|
| `id` | Source prefix + post ID, e.g. `nl-8123456`, `hnj-996323`, `mjm-9d371624b4` |
| `category` | One of: Accounting, Engineering, IT/Tech, Sales/Marketing, Customer Service, Admin/Office, Healthcare, Education, Driving/Logistics, Government, NGO, Internship/Graduate, Other |
| `location` | e.g. `Lagos`, `Ikeja, Lagos`, `Lagos, Abuja`, `Remote`; `Nigeria` if no place is found |
| `company` | `"Not stated"` when the ad doesn't name one |
| `salary`, `deadline` | `null` when not found. `deadline` is `YYYY-MM-DD` |
| `posted_at` | ISO 8601, UTC |
| `red_flags` | Short, human-readable warnings. An empty list means no warnings |

The data has these properties:

- **Size and order:** at most 1,000 jobs, newest first, 300 per page.
- **Pruning:** jobs older than 30 days, or past their deadline, are removed.
- **Duplicates:** jobs with the same title, company and location are merged into one.
- **Stable files:** if nothing changed, the files stay byte-for-byte identical, so there are no empty commits.
- **Processed posts:** [`data/seen.json`](data/seen.json) remembers which posts have been processed, for 60 days.

## Run it locally

You need Python 3.10 or newer; GitHub Actions uses 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the tests (offline, they use the saved pages in tests/fixtures/)
pytest

# Dry run against the real sites, writing to a temporary folder instead of docs/ and data/
python -m scraper --out-dir /tmp/jobafriq/docs --data-dir /tmp/jobafriq/data

# Real run (updates docs/ and data/)
python -m scraper
```

Useful options:

| Option | Effect |
|---|---|
| `--only nairaland myjobmag` | Run only some sources |
| `--no-ai` | Never call Gemini |
| `--report report.json` | Save a run report |
| `-v` | Verbose logs |

To try Gemini locally, run `export GEMINI_API_KEY=...` before `python -m scraper`.

## Publish with GitHub Pages

1. Push this repository to GitHub. It must be public for free GitHub Pages.
2. On GitHub, open **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to *Deploy from a branch*. Then set **Branch** to `main` and the folder to **`/docs`**, and click **Save**.
4. Open **Settings → Actions → General → Workflow permissions** and make sure **Read and write permissions** is selected. The workflow file also requests `contents: write`, but an organisation-level setting can override that.
5. Open the **Actions** tab, choose **Scrape jobs** and click **Run workflow** to run it once straight away. After that it runs every hour, at minute 17 (UTC).

Your app should download:

```
https://<your-github-username>.github.io/<repository-name>/latest.json
```

For example: `https://janedoe.github.io/jobAfriq/latest.json`. Fetch later pages by replacing `latest.json` with the value of `next`, such as `.../page-2.json`.

Note that GitHub can pause scheduled workflows in repositories with no activity for 60 days. If that happens, re-enable the workflow in the Actions tab.

## Optional: Gemini summaries

Without a key, summaries are built from the extracted fields. For example: *"Greenfield Foods Limited is hiring an Accountant in Ikeja, Lagos. 3+ years experience required; pay: ₦250,000 - ₦300,000 per month."*

With a key, Gemini writes a more natural one- or two-sentence summary and tidies the fields. The full ad text is never copied into the output, and summaries that repeat long phrases from the ad are rejected.

1. Get an API key at <https://aistudio.google.com/apikey>.
2. In your repository, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Set **Name** to `GEMINI_API_KEY` and **Secret** to your key.
4. Optional: to choose a different model, add a repository **variable** (not a secret) named `GEMINI_MODEL`, e.g. `gemini-2.5-flash`. The default is `gemini-flash-latest`.

Gemini calls are limited to 60 per run and spaced 4.5 seconds apart, so the free tier is enough. You can change both values in `sources.yaml` under `settings.ai`. If Gemini fails or hits a rate limit, the run carries on with built-in summaries.

## Turn sources on or off, or add a new source

All sources are in [`sources.yaml`](sources.yaml):

- **To turn a source off**, set `enabled: false`.
- **To add another RSS job feed** (no code needed), add a block like this:

  ```yaml
  - name: examplejobs          # unique, lowercase, no spaces
    type: rss
    label: ExampleJobs         # shown in "Open the full ad on ExampleJobs..."
    enabled: true
    prefix: exj                # ID prefix -> "exj-12345"
    url: https://www.examplejobs.ng/feed/
    id_pattern: "/jobs/(\\d+)" # optional regex on the item link; group 1 = ID (else a hash of the link)
    max_items: 100
  ```

  Then do a dry run to check it, e.g. `python -m scraper --only examplejobs --out-dir /tmp/t --data-dir /tmp/t`. Quote YAML values such as `"on"`, `"off"` or `"no"`, because YAML otherwise reads them as true or false.

- **To add a new kind of site** (HTML pages instead of RSS):
  1. Create `scraper/sources/mysite.py` with a class that extends `Source` and implements `fetch(session, seen)`. It should yield `RawPost` objects; use [`nairaland.py`](scraper/sources/nairaland.py) as the example.
  2. Always fetch through `session.get(...)`, which handles robots.txt, delays, retries and block detection.
  3. Register the class in [`scraper/sources/__init__.py`](scraper/sources/__init__.py) (`SOURCE_TYPES`), and add a config block with `type: mysite`.
  4. Save a sample page in `tests/fixtures/` and add a test.

## Polite scraping

- **robots.txt:** it is checked for every site, including `*` and `$` wildcards, and disallowed paths are never fetched.
- **Identification:** every request uses the User-Agent `JobAfriqBot/1.0 (free job-alert app for Nigerian job seekers)`. Consider adding your app or repo URL to it in `sources.yaml`.
- **Pacing:** there is a random 1.5–2 second wait between requests, a 20-second timeout, and at most 2 retries (none if the site serves a bot challenge).
- **Nairaland limits:** only the first 2 board pages are read, at most 40 new threads are opened per run, and only the first post of each thread is used. Threads already in `seen.json`, and old threads that were bumped back to the top, are skipped.
- **Failures:** if one source or one post fails, it is logged and the run continues. The run report is shown on each workflow run's summary page.

## Known limitations

- **Nairaland uses Cloudflare bot protection.** During development, some requests were blocked with a "Just a moment…" challenge (from `curl`), while the scraper's own requests got through. If the protection becomes stricter, or GitHub's servers get challenged, the Nairaland source logs `served a bot challenge` and returns nothing, while the other sources keep working. The scraper does not try to get around the challenge.
- **HotNigerianJobs' robots.txt** sets `Content-Signal: search=yes, ai-train=no, use=reference`. Showing short summaries with a link back to the full ad fits that.
- **Extraction is rule-based** (regex and keywords), so some fields will be wrong now and then. Red flags are warnings, not verdicts.

## Project layout

```
scraper/            Python package (python -m scraper)
  sources/          one module per kind of source (nairaland.py, rss.py)
  http.py           polite HTTP client (robots.txt, delays, retries, block detection)
  extract.py        regex/keyword extraction: contacts, salary, deadline, location, category...
  redflags.py       scam warning rules
  summarize.py      Gemini + built-in summaries
  jobs.py           builds the final job record
  store.py          merge, dedupe, prune, paginate, write files, seen.json
sources.yaml        list of sources and settings
tests/              pytest tests; tests/fixtures/ has saved pages and feeds
docs/               published JSON (GitHub Pages), with .nojekyll
data/seen.json      IDs already processed
.github/workflows/scrape.yml
```
