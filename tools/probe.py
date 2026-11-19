import re, requests
H = {"User-Agent": "Mozilla/5.0 (hse-ed-tracker probe)"}
base = "https://www2.hse.ie/services/urgent-and-emergency-care-report-daily-archive/"
for q in ("?page=2", "?p=2", "?year=2025"):
    r = requests.get(base + q, headers=H, timeout=30)
    names = re.findall(r'Urgent_and_Emergency_Care_-_[\w]+\.pdf', r.text)
    print("PAGE", q, r.status_code, names[:2], names[-1:] if names else "")
pag = re.findall(r'href="([^"]*(?:page|p)=\d+[^"]*)"', requests.get(base, headers=H, timeout=30).text)
print("PAGINATION LINKS", pag[:10])
for i in list(range(11500, 11509)) + [11450, 11400, 11300, 11200, 11000, 10500, 10000]:
    r = requests.head(f"https://www2.hse.ie/documents/{i}/x.pdf", headers=H, timeout=30, allow_redirects=True)
    print("DOC", i, r.status_code, r.url.split("/")[-1][:70])
