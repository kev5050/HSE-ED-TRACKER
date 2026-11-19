"""Diagnostic: list document links on the HSE daily archive page."""
import re
import requests
H = {"User-Agent": "Mozilla/5.0 (hse-ed-tracker probe)"}
url = "https://www2.hse.ie/services/urgent-and-emergency-care-report-daily-archive/"
r = requests.get(url, headers=H, timeout=30)
t = r.text
print(f"=== {url} -> {r.status_code}, {len(t)} chars")
links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', t, re.S)
n = 0
for href, text in links:
    txt = re.sub(r"<[^>]+>|\s+", " ", text).strip()
    if any(k in href.lower() for k in ("pdf", "document", "download", "media", "asset")):
        n += 1
        if n <= 25 or n % 50 == 0:
            print(f"DOC {href} | {txt[:90]}")
print("TOTAL DOC LINKS", n)
for kw in ("inset-text", "Get a report"):
    for mm in list(re.finditer(re.escape(kw), t))[:2]:
        print("CTX", re.sub(r"\s+", " ", t[mm.start(): mm.end()+700]))
# Follow first doc link to see what it serves
first = next((h for h, _ in links if "pdf" in h.lower() or "document" in h.lower()), None)
if first:
    full = requests.compat.urljoin(url, first)
    rr = requests.get(full, headers=H, timeout=30, allow_redirects=True)
    print("FIRST", rr.status_code, rr.headers.get("content-type"), rr.url, rr.content[:8])
