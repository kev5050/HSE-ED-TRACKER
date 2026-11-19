"""One-off diagnostic: what does the HSE report page contain, and do candidate URLs exist?"""
import re, sys
from datetime import date, timedelta
import requests

H = {"User-Agent": "Mozilla/5.0 (hse-ed-tracker probe)"}
PAGES = ["https://www2.hse.ie/services/urgent-emergency-care-report/",
         "https://www.hse.ie/services/urgent-emergency-care-report/"]
for url in PAGES:
    try:
        r = requests.get(url, headers=H, timeout=30)
    except Exception as e:
        print("PAGE", url, "ERR", e); continue
    t = r.text
    print(f"\n=== PAGE {url} -> {r.status_code}, {len(t)} chars, final={r.url}")
    urls = sorted(set(re.findall(r'(?:href|src|action|data-[\w-]+)="([^"]+)"', t)))
    for u in urls:
        if any(k in u.lower() for k in ("pdf", "report", "download", "api", "asset", "urgent", "trolley", "sdu", ".js")):
            print("  LINK", u)
    for m in sorted(set(re.findall(r'https?://[^\s"\'<>]+', t))):
        if any(k in m.lower() for k in ("pdf", "api", "download", "sdu", "report")):
            print("  RAW ", m)
    # Show context around any date picker / form
    for kw in ("date", "picker", "Get a report", "SDUReport", "download-file"):
        for mm in list(re.finditer(re.escape(kw), t))[:3]:
            s = t[max(0, mm.start()-200): mm.end()+300].replace("\n", " ")
            print(f"  CTX[{kw}] ...{s}...")

d = date.today() - timedelta(days=1)
cands = [
  f"https://assets.hse.ie/media/documents/SDUReport{d.year}_{d.day:02d}_{d.month:02d}_{d.year}_0800.pdf",
  "https://assets.hse.ie/media/documents/SDUReport2023_19_12_2023_1400.pdf",
  f"https://assets.hse.ie/media/documents/SDUReport2025_{d.day:02d}_{d.month:02d}_2025_0800.pdf",
]
print()
for u in cands:
    try:
        r = requests.head(u, headers=H, timeout=30, allow_redirects=True)
        print("HEAD", r.status_code, r.headers.get("content-type"), u)
    except Exception as e:
        print("HEAD ERR", u, e)
