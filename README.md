# HSE ED Tracker

Pulls the HSE's daily *Urgent and Emergency Care - Hospital Status* report (PDF),
turns it into clean data, and keeps a history so each hospital can be compared
to its own normal.

**What the data is (and isn't):** the HSE report counts admitted patients waiting
on trolleys for a bed, plus delayed discharges and surge beds. It does **not**
say how long a walk-in patient waits to be seen. The site should present it as
"how busy is this ED today vs usual", not as a wait time.

## How it works

```
HSE PDF ──► fetch.py ──► parse.py ──► validate (totals must add up) ──► store.py
                                                                          │
              data/history/2026.csv  ◄──────────────────────────────────┤
              data/latest.json       ◄── what the website reads ──────────┤
              data/hospitals.json    ◄── every hospital + has_ed flag ────┘
```

- **Fetching**: reads the
  [HSE daily archive page](https://www2.hse.ie/services/urgent-and-emergency-care-report-daily-archive/),
  which lists the last 14 days of reports, and also tries the file address directly:
  `assets.hse.ie/media/documents/Urgent_and_Emergency_Care_-_18_November_2026.pdf`.
  The HSE publishes **one report a day** (08:00 snapshot) and **deletes reports
  older than ~14 days**, so this repo is the only long-term history. Don't lose it.
- **Parsing**: reads numbers by column position, because hospitals without an
  ED only fill some columns. Every report is checked: hospitals must sum to
  region totals and regions to the national total, or it's rejected.
- **Re-publishing**: the 08:00 report gets updated during the day (surge beds are
  added at 14:00). Today's PDFs are re-checked hourly and replaced if they change.
- **"vs usual"**: `latest.json` compares each hospital's trolley count with its
  daily average over the previous 28 days (needs 7+ days of history).

## Running locally

```bash
git clone <your repo> && cd hse-ed-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                      # tests should all pass
python -m edtracker update               # fetch the latest reports
```

## If it breaks

GitHub emails you when a run fails. Usual causes:
- **"0 PDFs found" / STALE**: the HSE moved the archive page or renamed files.
  Open the archive page in a browser, copy a PDF link, and update `INDEX_URLS`
  and `URL_TEMPLATES` in `edtracker/fetch.py`.
- **"totals don't reconcile" / "parse failed"**: the PDF layout changed. The PDF
  is saved in `data/failed/`; run `python -m edtracker parse <file>` to see what
  the parser reads, and adjust `edtracker/parse.py`.

### Status (19 Nov 2026)

Live. The hourly GitHub job is fetching reports and history starts on
5 November 2026 (the oldest report the HSE still had online).

### Running it on GitHub

Commit everything (including `data/`) and push. `.github/workflows/update.yml`
runs hourly 07:15-21:15 UTC, commits any new data, and is free on a public repo.
Trigger a first run from the repo's **Actions** tab → *Fetch HSE report* →
*Run workflow*.

If the HSE changes their URL or layout, the run fails and GitHub emails you.
It also fails if no new report has arrived for 2+ days.

## Commands

| Command | What it does |
|---|---|
| `python -m edtracker update` | Fetch yesterday + today (what the schedule runs) |
| `python -m edtracker backfill 2026-11-01 [end]` | Fetch a date range (only last ~14 days exist) |
| `python -m edtracker ingest file.pdf ...` | Add PDFs you downloaded by hand |
| `python -m edtracker parse file.pdf` | Print what the parser reads (debugging) |
| `python -m edtracker rebuild` | Regenerate `latest.json` / `hospitals.json` |

## Data columns

| Column | Meaning (HSE definition, paraphrased) |
|---|---|
| `ed_trolleys` | Admitted patients on trolleys in the ED waiting for a bed |
| `ward_trolleys` | Admitted patients on trolleys on a ward waiting for a bed |
| `total_trolleys` | ED + ward |
| `surge_in_use` | Temporary extra beds open (reported at 14:00, blank at 08:00) |
| `delayed_transfers` | Patients medically fit to leave but still in a bed (as of midnight) |
| `waiting_over_24h` | Trolley patients waiting more than 24 hours |
| `over75_over_24h` | Of those, patients aged 75+ |

Blank = the hospital doesn't report that figure. Hospitals with `has_ed: false`
in `hospitals.json` (Cappagh, NRH, Bantry, etc.) have no 24-hour ED and shouldn't
appear in the ED rankings.

## Website

`site/` is a static page (plain HTML, CSS and JS, no build step) that reads
`data/latest.json`, `data/history.json` and `data/locations.json`.
`.github/workflows/pages.yml` publishes it to GitHub Pages on every change and
after every hourly fetch.

- Preview locally: `mkdir -p _site/data && cp -r site/* _site/ && cp data/latest.json data/history.json data/locations.json _site/data/ && python -m http.server -d _site`
- Hospital coordinates and towns live in `data/locations.json` (hand-maintained,
  approximate - check them). `children: true` marks children's EDs.
- The site name is a placeholder ("ED Today") in `site/index.html`.

## Next up

- Custom domain once a name is picked.
- Same-weekday comparison for "vs usual" once there are ~6 weeks of history.
