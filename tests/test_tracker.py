import json
from pathlib import Path

import pytest

from edtracker import store
from edtracker.parse import Report, Row, parse_pdf, validate

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "SDUReport2026_15_11_2026_0800.pdf"


@pytest.fixture(scope="module")
def report():
    return parse_pdf(SAMPLE)


def test_header(report):
    assert report.snapshot_id == "2026-11-15T08:00"


def test_counts(report):
    assert len(report.hospitals()) == 42
    assert len(report.regions()) == 6


def test_national_totals(report):
    n = report.national().values
    assert (n["ed_trolleys"], n["ward_trolleys"], n["total_trolleys"]) == (172, 46, 218)
    assert n["delayed_transfers"] == 561
    assert n["waiting_over_24h"] == 32 and n["over75_over_24h"] == 1


def test_reconciles(report):
    assert validate(report) == []


def by_name(report, name):
    return next(h for h in report.hospitals() if h.name == name)


def test_partial_rows_land_in_right_column(report):
    # Louth only reports delayed transfers; its lone 0 must not be read as ED trolleys.
    louth = by_name(report, "Louth County Hospital").values
    assert louth["ed_trolleys"] is None and louth["delayed_transfers"] == 0
    # Navan has no >24h value but does have the 75+ value.
    navan = by_name(report, "Our Lady's Hospital Navan").values
    assert navan["waiting_over_24h"] is None and navan["over75_over_24h"] == 0


def test_limerick(report):
    v = by_name(report, "UH Limerick").values
    assert (v["ed_trolleys"], v["ward_trolleys"], v["total_trolleys"]) == (7, 26, 33)
    assert v["waiting_over_24h"] == 9 and v["over75_over_24h"] == 1
    assert by_name(report, "UH Limerick").region == "HSE Mid West"


def test_store_and_baseline(tmp_path, monkeypatch, report):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "HISTORY", tmp_path / "history")
    # 28 synthetic prior days where UH Limerick had 10 trolleys at 08:00.
    for day in range(16, 31):
        fake = Report("08:00", f"2026-10-{day:02d}",
                      [Row("UH Limerick", "hospital", "HSE Mid West",
                           {**by_name(report, "UH Limerick").values, "total_trolleys": 10})])
        store.upsert_report(fake)
    store.upsert_report(report)
    store.upsert_report(report)  # idempotent: re-upserting replaces, not duplicates
    store.rebuild_outputs()

    latest = json.loads((tmp_path / "latest.json").read_text())
    assert latest["snapshot_id"] == "2026-11-15T08:00"
    lim = next(h for h in latest["hospitals"] if h["id"] == "uh-limerick")
    assert lim["usual_total_trolleys"] == 10.0
    assert lim["pressure"] == "much busier than usual"   # 33 vs usual 10
    rows = store.read_history()
    assert sum(r["snapshot_id"] == "2026-11-15T08:00" for r in rows) == len(report.rows)


def test_pressure_labels():
    assert store._pressure(5, None) is None
    assert store._pressure(10, 10) == "usual"
    assert store._pressure(12, 10) == "busier than usual"
    assert store._pressure(2, 10) == "quieter than usual"
    assert store._pressure(0, 0.2) == "usual"
