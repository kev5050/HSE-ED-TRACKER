"""Download HSE report PDFs.

Two discovery routes, because the HSE could change either:
 1. The daily archive page, which links the last ~14 days of reports.
 2. Known URL patterns on assets.hse.ie, used to backfill older days:
      Urgent_and_Emergency_Care_-_18_November_2026.pdf   (current, one per day)
      SDUReport2023_19_12_2023_1400.pdf                   (older naming)
If both stop working, open INDEX_URLS in a browser, copy a PDF link,
and update URL_TEMPLATES / INDEX_URLS below.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date
from urllib.parse import urljoin

import requests

INDEX_URLS = [
    "https://www2.hse.ie/services/urgent-and-emergency-care-report-daily-archive/",
    "https://www2.hse.ie/services/urgent-emergency-care-report/",
]
URL_TEMPLATES = [
    "https://assets.hse.ie/media/documents/Urgent_and_Emergency_Care_-_{d}_{month}_{Y}.pdf",
    "https://assets.hse.ie/media/documents/SDUReport{Y}_{dd}_{mm}_{Y}_{slot}.pdf",
]
SLOTS = ["0800"]
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
                         d=day.day, m=day.month, month=day.strftime("%B"), slot=slot)
            if u not in urls:
                urls.append(u)
    return urls


def index_pdf_links() -> list[str]:
    """PDF links on the HSE pages, newest first (as the archive lists them)."""
    links = []
    for page in INDEX_URLS:
        try:
            r = session.get(page, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  index page unavailable: {page}: {e}")
            continue
        for h in PDF_LINK_RE.findall(r.text):
            u = urljoin(page, h)
            if u not in links:
                links.append(u)
    print(f"  {len(links)} PDF links on HSE pages")
    return links


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
