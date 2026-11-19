"""Parse the HSE 'Urgent and Emergency Care - Hospital Status' PDF.

The report is a table. Some hospitals only report a subset of columns
(e.g. hospitals with no ED only report delayed transfers of care), so we
can't read values left-to-right. Instead every number is assigned to the
column whose header it sits under, using x positions from the PDF.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import pdfplumber

# Column keys, in report order.
COLUMNS = [
    "ed_trolleys",        # patients on ED trolleys with a decision to admit
    "ward_trolleys",      # patients on trolleys on a ward awaiting a bed
    "total_trolleys",     # ED + ward
    "surge_in_use",       # extra temporary beds in use (reported at 14:00)
    "delayed_transfers",  # ready for discharge but still in a bed (as of midnight)
    "waiting_over_24h",   # trolley patients waiting > 24 hrs
    "over75_over_24h",    # of those, aged 75+
]

# Fallback column centres (x, in PDF points) measured from the Nov 2026 layout.
# Used only if a header can't be found on the page.
DEFAULT_CENTRES = {
    "ed_trolleys": 390,
    "ward_trolleys": 441,
    "total_trolleys": 492,
    "surge_in_use": 556,
    "delayed_transfers": 630,
    "waiting_over_24h": 706,
    "over75_over_24h": 782,
}

NAME_MAX_X = 370          # anything right of this is a number column
ROW_TOLERANCE = 4         # points; values in one row can be ~1pt apart vertically
INT_RE = re.compile(r"^\d+$")
STAMP_RE = re.compile(r"(\d{2}:\d{2})\s+\w+\s+-\s+(\d{2}/\d{2}/\d{4})")
CHO_SUFFIX_RE = re.compile(r"\s*-\s*\[CHO\s*\d+\]\s*$", re.I)


@dataclass
class Row:
    name: str
    kind: str                      # "hospital" | "region_total" | "national_total"
    region: str | None
    values: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class Report:
    snapshot_time: str             # "08:00"
    report_date: str               # ISO "2026-11-15"
    rows: list[Row]

    @property
    def snapshot_id(self) -> str:
        return f"{self.report_date}T{self.snapshot_time}"

    def hospitals(self):
        return [r for r in self.rows if r.kind == "hospital"]

    def regions(self):
        return [r for r in self.rows if r.kind == "region_total"]

    def national(self):
        return next((r for r in self.rows if r.kind == "national_total"), None)


def _group_rows(words):
    """Cluster words into visual rows by their 'top' coordinate."""
    rows = []
    for w in sorted(words, key=lambda w: w["top"]):
        if rows and abs(w["top"] - rows[-1][0]) <= ROW_TOLERANCE:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in rows]


def _centre(w):
    return (w["x0"] + w["x1"]) / 2


def _find_centres(words):
    """Locate column centres from header words; fall back to defaults."""
    centres = dict(DEFAULT_CENTRES)
    header = [w for w in words if w["top"] < 140 and w["x0"] > NAME_MAX_X]

    def pick(text, key, min_x=0, max_x=10_000):
        hits = [w for w in header if w["text"] == text and min_x <= w["x0"] <= max_x]
        if hits:
            centres[key] = _centre(hits[0])

    pick("ED", "ed_trolleys", max_x=420)
    pick("Ward", "ward_trolleys")
    pick("Transfers", "delayed_transfers")
    pick("+yrs", "over75_over_24h")
    # 'Total' appears twice in the header: trolley total (bottom row) and
    # 'No of Total Waiting' (upper). Use position to tell them apart.
    totals = sorted((w for w in header if w["text"] == "Total"), key=_centre)
    if totals:
        centres["total_trolleys"] = _centre(totals[0])
    if len(totals) > 1:
        centres["waiting_over_24h"] = _centre(totals[-1])
    caps = [w for w in header if w["text"] == "Capacity"]
    if caps:
        centres["surge_in_use"] = _centre(caps[0])
    return centres


def _assign(numbers, centres):
    values = {k: None for k in COLUMNS}
    for w in numbers:
        x = _centre(w)
        key = min(centres, key=lambda k: abs(centres[k] - x))
        if abs(centres[key] - x) > 30:
            raise ValueError(f"Number {w['text']!r} at x={x:.0f} matches no column")
        if values[key] is not None:
            raise ValueError(f"Two numbers landed in column {key}")
        values[key] = int(w["text"])
    return values


def clean_name(name: str) -> str:
    name = CHO_SUFFIX_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_pdf(path: str | Path) -> Report:
    rows: list[Row] = []
    stamp = None
    region = None

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            centres = _find_centres(words)
            for line in _group_rows(words):
                text = " ".join(w["text"] for w in line)
                if stamp is None and (m := STAMP_RE.search(text)):
                    stamp = m.groups()
                if text.startswith("Definitions:"):
                    break  # footer; nothing below is data
                # Skip page header block.
                if line[0]["top"] < 140:
                    continue
                name_words = [w for w in line if w["x1"] <= NAME_MAX_X]
                num_words = [w for w in line if w["x0"] > NAME_MAX_X and INT_RE.match(w["text"])]
                if not name_words:
                    continue
                name = clean_name(" ".join(w["text"] for w in name_words))
                if name.startswith("Page "):
                    continue

                if not num_words:
                    # A region heading such as "HSE Dublin & North East".
                    region = name
                    continue

                values = _assign(num_words, centres)
                if name == "National Total":
                    rows.append(Row(name, "national_total", None, values))
                elif name.endswith(" Total"):
                    rows.append(Row(name[: -len(" Total")], "region_total", None, values))
                else:
                    rows.append(Row(name, "hospital", region, values))

    if stamp is None:
        raise ValueError("Could not find the report timestamp (e.g. '08:00 Sunday - 15/11/2026')")
    time_str, date_str = stamp
    iso_date = datetime.strptime(date_str, "%d/%m/%Y").date().isoformat()
    return Report(time_str, iso_date, rows)


def validate(report: Report) -> list[str]:
    """Check that hospital rows add up to region totals and regions to national.

    Returns a list of problems; empty means the parse reconciles.
    """
    problems = []
    if not report.hospitals():
        return ["No hospital rows parsed"]
    sum_cols = ["ed_trolleys", "ward_trolleys", "total_trolleys", "delayed_transfers",
                "waiting_over_24h", "over75_over_24h"]

    def total(rows, col):
        return sum(r.values[col] or 0 for r in rows)

    for reg in report.regions():
        members = [h for h in report.hospitals() if h.region == reg.name]
        if not members:
            problems.append(f"Region total '{reg.name}' has no hospitals")
            continue
        for col in sum_cols:
            if reg.values[col] is not None and total(members, col) != reg.values[col]:
                problems.append(
                    f"{reg.name} {col}: hospitals sum {total(members, col)} != total {reg.values[col]}")

    nat = report.national()
    if nat:
        for col in sum_cols:
            if nat.values[col] is not None and total(report.regions(), col) != nat.values[col]:
                problems.append(
                    f"National {col}: regions sum {total(report.regions(), col)} != total {nat.values[col]}")
    else:
        problems.append("No national total row")

    for h in report.hospitals():
        v = h.values
        if None not in (v["ed_trolleys"], v["ward_trolleys"], v["total_trolleys"]):
            if v["ed_trolleys"] + v["ward_trolleys"] != v["total_trolleys"]:
                problems.append(f"{h.name}: ED + ward != total")
    return problems
