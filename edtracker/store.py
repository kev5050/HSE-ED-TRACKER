"""Flat-file storage: yearly CSVs of every snapshot + JSON the website reads.

data/
  history/2026.csv     one row per hospital/region/national per snapshot
  latest.json          most recent snapshot with 'vs usual' comparison (site reads this)
  hospitals.json       registry of every hospital seen, with has_ed flag
  state.json           which PDFs we've already processed (by content hash)
"""
from __future__ import annotations

import csv
import json
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .parse import COLUMNS, Report

DATA = Path(__file__).resolve().parent.parent / "data"
HISTORY = DATA / "history"
FIELDS = ["snapshot_id", "report_date", "snapshot_time", "kind", "region",
          "hospital_id", "hospital"] + COLUMNS
BASELINE_DAYS = 28


def slugify(name: str) -> str:
    s = name.lower().replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# Old reports used different names for some sites. Map them to one id here
# as they're found, e.g. "roscommon-hospital": "roscommon-university-hospital".
ALIASES: dict[str, str] = {}


def hospital_id(name: str) -> str:
    sid = slugify(name)
    return ALIASES.get(sid, sid)


# ---------- state ----------

def load_state() -> dict:
    p = DATA / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"processed": {}, "errors": []}


def save_state(state: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True))


# ---------- history ----------

def _year_file(year: str) -> Path:
    return HISTORY / f"{year}.csv"


def read_history(years=None) -> list[dict]:
    rows = []
    if not HISTORY.exists():
        return rows
    for f in sorted(HISTORY.glob("*.csv")):
        if years and f.stem not in years:
            continue
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                for c in COLUMNS:
                    r[c] = int(r[c]) if r[c] not in ("", None) else None
                rows.append(r)
    return rows


def upsert_report(report: Report):
    """Write a report into its year's CSV, replacing any earlier version of
    the same snapshot (the HSE updates the 08:00 report during the morning)."""
    HISTORY.mkdir(parents=True, exist_ok=True)
    year = report.report_date[:4]
    path = _year_file(year)
    existing = [r for r in read_history([year]) if r["snapshot_id"] != report.snapshot_id]
    new = []
    for row in report.rows:
        new.append({
            "snapshot_id": report.snapshot_id,
            "report_date": report.report_date,
            "snapshot_time": report.snapshot_time,
            "kind": row.kind,
            "region": row.region or "",
            "hospital_id": hospital_id(row.name) if row.kind == "hospital" else "",
            "hospital": row.name,
            **row.values,
        })
    allrows = sorted(existing + new, key=lambda r: (r["snapshot_id"], r["kind"] != "national_total",
                                                    r["region"], r["hospital"]))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in allrows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})


# ---------- derived outputs ----------

def _pressure(today: int | None, usual: float | None) -> str | None:
    """Simple label comparing today's trolley count to the hospital's own norm."""
    if today is None or usual is None:
        return None
    if usual < 1:
        return "busier than usual" if today >= 3 else "usual"
    ratio = today / usual
    if ratio >= 1.5:
        return "much busier than usual"
    if ratio >= 1.15:
        return "busier than usual"
    if ratio <= 0.6:
        return "quieter than usual"
    return "usual"


def rebuild_outputs():
    rows = read_history()
    if not rows:
        return
    hospitals = {}
    for r in rows:
        if r["kind"] != "hospital":
            continue
        h = hospitals.setdefault(r["hospital_id"], {
            "id": r["hospital_id"], "name": r["hospital"], "region": r["region"],
            "has_ed": False, "first_seen": r["report_date"], "last_seen": r["report_date"],
        })
        h["name"], h["region"] = r["hospital"], r["region"]   # keep latest naming
        h["last_seen"] = max(h["last_seen"], r["report_date"])
        h["first_seen"] = min(h["first_seen"], r["report_date"])
        if r["ed_trolleys"] is not None:
            h["has_ed"] = True
    (DATA / "hospitals.json").write_text(json.dumps(
        sorted(hospitals.values(), key=lambda h: h["name"]), indent=2))

    latest_id = max(r["snapshot_id"] for r in rows)
    latest = [r for r in rows if r["snapshot_id"] == latest_id]
    slot = latest[0]["snapshot_time"]
    end = date.fromisoformat(latest[0]["report_date"])
    start = end - timedelta(days=BASELINE_DAYS)

    # Baseline: one reading per day (that day's latest report) over the
    # previous 28 days, per hospital. The HSE publishes one report a day, but
    # occasionally re-issues it later (e.g. stamped 14:00).
    per_day: dict[tuple, dict] = {}
    for r in rows:
        if (r["kind"] == "hospital" and r["total_trolleys"] is not None
                and start <= date.fromisoformat(r["report_date"]) < end):
            key = (r["hospital_id"], r["report_date"])
            if key not in per_day or r["snapshot_time"] > per_day[key]["snapshot_time"]:
                per_day[key] = r
    window: dict[str, list[int]] = {}
    for (hid, _), r in per_day.items():
        window.setdefault(hid, []).append(r["total_trolleys"])

    out_hosp = []
    for r in latest:
        if r["kind"] != "hospital":
            continue
        hist = window.get(r["hospital_id"], [])
        usual = round(statistics.mean(hist), 1) if len(hist) >= 7 else None
        out_hosp.append({
            "id": r["hospital_id"], "name": r["hospital"], "region": r["region"],
            "has_ed": hospitals[r["hospital_id"]]["has_ed"],
            **{c: r[c] for c in COLUMNS},
            "usual_total_trolleys": usual,
            "baseline_days": len(hist),
            "pressure": _pressure(r["total_trolleys"], usual),
        })
    nat = next((r for r in latest if r["kind"] == "national_total"), None)
    payload = {
        "snapshot_id": latest_id,
        "report_date": latest[0]["report_date"],
        "snapshot_time": slot,
        "source": "HSE Urgent and Emergency Care report",
        "national": {c: nat[c] for c in COLUMNS} if nat else None,
        "regions": [{"name": r["hospital"], **{c: r[c] for c in COLUMNS}}
                    for r in latest if r["kind"] == "region_total"],
        "hospitals": sorted(out_hosp, key=lambda h: (-(h["total_trolleys"] or -1), h["name"])),
    }
    out = DATA / "latest.json"
    new = json.dumps(payload, indent=2)
    if not out.exists() or out.read_text() != new:   # avoid pointless hourly commits
        out.write_text(new)
