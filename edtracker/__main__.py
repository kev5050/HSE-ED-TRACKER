"""Command line.

  python -m edtracker update                 fetch today + yesterday, all time slots
  python -m edtracker backfill 2024-01-01    fetch every day from a date (slow, polite)
  python -m edtracker parse file.pdf         parse one PDF and print it (for debugging)
  python -m edtracker ingest file.pdf ...    add PDFs you downloaded by hand
  python -m edtracker rebuild                regenerate latest.json / hospitals.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import fetch, store
from .parse import parse_pdf, validate

RAW = store.DATA / "raw"


def process_bytes(pdf: bytes, source: str, state: dict) -> str:
    """Parse, validate and store one PDF. Returns 'new', 'unchanged' or 'error'."""
    digest = fetch.sha256(pdf)
    seen = state["processed"].get(source)
    if seen and seen["sha256"] == digest:
        return "unchanged"
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf)
        tmp.flush()
        try:
            report = parse_pdf(tmp.name)
        except Exception as e:  # noqa: BLE001 - want any parse failure recorded
            return _error(state, source, f"parse failed: {e}", pdf)
    problems = validate(report)
    if problems:
        return _error(state, source, "totals don't reconcile: " + "; ".join(problems[:5]), pdf)

    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{report.snapshot_id.replace(':', '')}.pdf").write_bytes(pdf)
    store.upsert_report(report)
    state["processed"][source] = {
        "sha256": digest,
        "snapshot_id": report.snapshot_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"  stored {report.snapshot_id} ({len(report.hospitals())} hospitals) from {source}")
    return "new"


def _error(state, source, msg, pdf):
    print(f"  ERROR {source}: {msg}")
    fail_dir = store.DATA / "failed"
    fail_dir.mkdir(parents=True, exist_ok=True)
    (fail_dir / (Path(source).name or "unknown.pdf")).write_bytes(pdf)
    state["errors"] = (state.get("errors", []) + [{
        "source": source, "error": msg,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])[-50:]
    return "error"


STALE_AFTER_DAYS = 2


def run_days(days, use_index: bool, check_stale: bool = False) -> int:
    state = store.load_state()
    today = date.today()
    results = []
    urls = []
    for d in days:
        for u in fetch.candidate_urls(d):
            # Today's reports can be re-published during the day (surge figures
            # get added), so re-check them. Older ones we already have are final.
            if d < today and u in state["processed"]:
                continue
            urls.append(u)
    if use_index:
        urls += [u for u in fetch.index_pdf_links() if u not in urls]
    for url in urls:
        pdf = fetch.download(url)
        if pdf is not None:
            results.append(process_bytes(pdf, url, state))
    save_and_rebuild(state)
    errors = results.count("error")
    print(f"done: {len(results)} PDFs found, {results.count('new')} new/updated, {errors} errors")
    if check_stale and is_stale(today):
        print(f"STALE: no new report for over {STALE_AFTER_DAYS} days - "
              "the HSE URL or page may have changed. See README > 'If it breaks'.")
        return 1
    return 1 if errors else 0


def is_stale(today: date) -> bool:
    snaps = [v["snapshot_id"] for v in store.load_state()["processed"].values()]
    if not snaps:
        return True
    return (today - date.fromisoformat(max(snaps)[:10])).days > STALE_AFTER_DAYS


def save_and_rebuild(state):
    store.save_state(state)
    store.rebuild_outputs()


def main(argv=None):
    p = argparse.ArgumentParser(prog="edtracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    b = sub.add_parser("backfill")
    b.add_argument("start", type=date.fromisoformat)
    b.add_argument("end", type=date.fromisoformat, nargs="?", default=None)
    pp = sub.add_parser("parse")
    pp.add_argument("pdf")
    ing = sub.add_parser("ingest")
    ing.add_argument("pdfs", nargs="+")
    sub.add_parser("rebuild")
    a = p.parse_args(argv)

    if a.cmd == "update":
        today = date.today()
        return run_days([today - timedelta(days=1), today], use_index=True, check_stale=True)
    if a.cmd == "backfill":
        end = a.end or date.today()
        days = [a.start + timedelta(days=i) for i in range((end - a.start).days + 1)]
        print(f"backfilling {len(days)} days ({len(days) * len(fetch.SLOTS)} slots)")
        return run_days(days, use_index=False)
    if a.cmd == "parse":
        r = parse_pdf(a.pdf)
        print(json.dumps({"snapshot_id": r.snapshot_id, "problems": validate(r),
                          "rows": [x.to_dict() for x in r.rows]}, indent=2))
        return 0
    if a.cmd == "ingest":
        state = store.load_state()
        res = [process_bytes(Path(f).read_bytes(), f"file:{Path(f).name}", state) for f in a.pdfs]
        save_and_rebuild(state)
        return 1 if "error" in res else 0
    if a.cmd == "rebuild":
        store.rebuild_outputs()
        return 0


if __name__ == "__main__":
    sys.exit(main())
