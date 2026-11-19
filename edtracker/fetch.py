"""Download HSE report PDFs.

Two discovery routes, because the HSE could change either:
 1. Known URL pattern on assets.hse.ie (seen on reports since at least 2023):
      SDUReport2023_19_12_2023_1400.pdf  -> year, day, month, year, HHMM
 2. Scrape the public report page for any .pdf links.
If both stop working, check the report page in a browser, copy a PDF link,
and update URL_TEMPLATES / INDEX_URL below.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date
from urllib.parse import urljoin

import requests

INDEX_URL = "https://www2.hse.ie/services/urgent-emergency-care-report/"
URL_TEMPLATES = [
    "https://assets.hse.ie/media/documents/SDUReport{Y}_{dd}_{mm}_{Y}_{slot}.pdf",
    "https://assets.hse.ie/media/documents/SDUReport{Y}_{d}_{m}_{Y}_{slot}.pdf",
]
SLOTS = ["0800", "1400", "2000"]
HEADERS = {"User-Agent": "hse-ed-tracker/0.1 (open-data project; polite daily fetch)"}
TIMEOUT = 30
PDF_LINK_RE = re.compile(r'href="([^"]+?\.pdf)"', re.I)

session = requests.Session()
session.headers.update(HEADERS)


def candidate_urls(day: date, slots=SLOTS) -> list[str]:
    urls = []
    for slot in slots:
        for t in URL_TEMPLATES:
            u = t.format(Y=day.year, dd=f"{day.day:02d}", mm=f"{day.month:02d}",
                         d=day.day, m=day.month, slot=slot)
            if u not in urls:
                urls.append(u)
    return urls


def index_pdf_links() -> list[str]:
    """PDF links on the HSE report page. Empty list if the page can't be read."""
    try:
        r = session.get(INDEX_URL, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  index page unavailable: {e}")
        return []
    return sorted({urljoin(INDEX_URL, h) for h in PDF_LINK_RE.findall(r.text)})


def download(url: str) -> bytes | None:
    """Return PDF bytes, or None if it doesn't exist (404 or not a PDF)."""
    try:
        r = session.get(url, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"  error {url}: {e}")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        print(f"  unexpected response {r.status_code} for {url}")
        return None
    time.sleep(1)  # be polite
    return r.content


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
