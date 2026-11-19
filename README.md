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

- **Fetching**: tries the known URL pattern
  `assets.hse.ie/media/documents/SDUReport{YEAR}_{DD}_{MM}_{YEAR}_{HHMM}.pdf`
  for 08:00, 14:00 and 20:00, and also scrapes the
  [HSE report page](https://www2.hse.ie/services/urgent-emergency-care-report/)
  for PDF links.
- **Parsing**: reads numbers by column position, because hospitals without an
  ED only fill some columns. Every report is checked: hospitals must sum to
  region totals and regions to the national total, or it's rejected.
- **Re-publishing**: the 08:00 report gets updated during the day (surge beds are
  added at 14:00). Today's PDFs are re-checked hourly and replaced if they change.
- **"vs usual"**: `latest.json` compares each hospital's trolley count with its
  average at the same time of day over the previous 28 days (needs 7+ days of
  history, so run the backfill first).

## Setup (one-off, ~15 mins)

```bash
git clone <your repo> && cd hse-ed-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                      # 8 tests, should all pass
```

### 1. Check the URL pattern still works - DO THIS FIRST

The pattern above was confirmed on 2023 reports. I couldn't reach hse.ie from where
this was built, so it's untested against 2026 reports.

```bash
python -m edtracker update
```

- `stored 2026-11-19T08:00 (42 hospitals)` → it works, move on.
- `0 PDFs found` → open the HSE report page in a browser, pick a date, and copy
  the PDF's download link. Update `URL_TEMPLATES` in `edtracker/fetch.py` to match
  (keep `{Y}`, `{dd}`, `{mm}`, `{slot}` placeholders), then re-run.
- Also note **which time slots exist** in 2026 (08:00 only, or 14:00 / 20:00 too).
  Remove any that never appear from `SLOTS` in `fetch.py`.

### 2. Backfill history

```bash
python -m edtracker backfill 2025-01-01   # ~1 request/sec, takes a while
```

Start with a month to check it works before going back years. Older reports
used different hospital names (e.g. `Roscommon Hospital - [CHO 2]`) and the old
hospital groups instead of HSE regions. The `[CHO x]` suffix is stripped
automatically. Where a hospital's name changed, add a mapping to `ALIASES` in
`store.py` so its history joins up. Check `data/hospitals.json` for near-duplicates.

If a backfilled report fails validation, it's saved in `data/failed/` with the
reason in `data/state.json` → most likely an older layout the parser needs
adjusting for. Debug with:

```bash
python -m edtracker parse data/failed/<file>.pdf
```

### 3. Push to GitHub and switch on the schedule

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
| `python -m edtracker backfill 2025-01-01 [end]` | Fetch a date range |
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

## Next up

- Add coordinates and ED opening hours to `hospitals.json` (for "nearest ED").
- Build the site on top of `data/latest.json`.
