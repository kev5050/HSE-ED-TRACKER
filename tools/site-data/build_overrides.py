"""Add the road overrides to site/places.json.
Reads WORKDIR (run build_roads.py first) and the repo. No network.
Usage: python -X utf8 build_overrides.py WORKDIR REPO
"""
import json
import sys
from pathlib import Path

B, R = Path(sys.argv[1]), Path(sys.argv[2])


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- road overrides ----------
fixes = load(B / "road-fix.json")
places = load(R / "site/places.json")
for i, fix in fixes.items():
    row = places[int(i)]
    if row[0] != fix["name"] or row[1] != fix["county"]:
        sys.exit(f"place {i} is {row[:2]}, expected {fix['name']}")
    places[int(i)] = row[:5] + [{care: fix[care] for care in ("adult", "child") if care in fix}]
body = ",\n".join(json.dumps(p, ensure_ascii=False, separators=(",", ":")) for p in places)
(R / "site/places.json").write_text(f"[\n{body}\n]\n", encoding="utf-8", newline="\n")

print("overrides", len(fixes))
