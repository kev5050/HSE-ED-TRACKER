"""Search the site for every real place name and check where its first listed row sends people, by road.

For each OpenStreetMap town, village and suburb inside an Eircode routing area (so in the Republic),
runs the site's own search (findPlaces, the function behind the town box) in sandboxed Chrome
(checks/site_check.py, outside requests blocked) and reads the first row it lists. FAILS if that row
gives a main answer more than 30 minutes slower by road, in every speed model, than the quickest ED
for that age from the real place, unless the row is a different place more than 10 km away with the
same name, or its shown name starts with the typed name (Ross lists Rosslare). Those two are counted
and printed, not failed: the row shows its county and nothing is chosen without a tap. Any other way
of matching (a near spelling, a county, a new rule) must not send anyone that far. Also fails if a
few control names don't list themselves first, so a search that finds nothing can't pass.

Reads WORKDIR (fetch_sources.py downloads) and REPO. No network. Writes only an empty
folder in WORKDIR, which site_check.py asks for. Exits 1 on any failure.
Run after rebuilding the data, and after any change to how the site searches.
Usage: python -X utf8 check_names.py WORKDIR REPO
"""
import importlib.util
import json
import math
import re
import struct
import sys
import unicodedata
from pathlib import Path

work, repo = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
SLOWER_MIN = 30
SAME_PLACE_KM = 10
CONTROLS = {"Navan": "Navan, Co. Meath", "Drogheda": "Drogheda, Co. Louth", "Rathtoe": "Rathtoe, Co. Carlow",
            "Bellavary": "Bellavary, Co. Mayo", "Kinsaley": "Kinsaley, Co. Dublin"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm(s):  # the site's simple()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


roads_mod = load("build_roads", repo / "tools/site-data/build_roads.py")
sys.argv = ["site_check.py", str(work / "check-names-unused"), str(repo)]
sc = load("site_check", repo / "checks/site_check.py")

# ---------- Eircode routing areas, to keep only places in the Republic ----------
sd = work / "autoaddr/shp"
dbf = (sd / "RoutingKeys_WGS84_region.dbf").read_bytes()
count, header_len, rec_len = struct.unpack("<IHH", dbf[4:12])
shp = (sd / "RoutingKeys_WGS84_region.shp").read_bytes()
p, AREAS = 100, []
for _ in range(count):
    size = struct.unpack(">i", shp[p + 4:p + 8])[0] * 2
    c = shp[p + 8:p + 8 + size]
    p += 8 + size
    n_parts, n_points = struct.unpack("<2i", c[36:44])
    parts = list(struct.unpack(f"<{n_parts}i", c[44:44 + 4 * n_parts])) + [n_points]
    start = 44 + 4 * n_parts
    xy = struct.unpack(f"<{2 * n_points}d", c[start:start + 16 * n_points])
    pts = list(zip(xy[::2], xy[1::2]))
    AREAS.append((struct.unpack("<4d", c[4:36]), [pts[parts[j]:parts[j + 1]] for j in range(n_parts)]))


def in_republic(lat, lon):
    for (x0, y0, x1, y1), rings in AREAS:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        crossings = sum(1 for ring in rings for (ax, ay), (bx, by) in zip(ring, ring[1:])
                        if (ay > lat) != (by > lat) and lon < ax + (lat - ay) * (bx - ax) / (by - ay))
        if crossings % 2:
            return True
    return False


real = [{"typed": e["tags"].get("name:en") or e["tags"]["name"], "lat": e["lat"], "lon": e["lon"]}
        for e in json.loads((work / "opendata/suburbs.json").read_text(encoding="utf-8"))["elements"]
        if in_republic(e["lat"], e["lon"])]

# ---------- road minutes from every ED, in every speed model ----------
locations = json.loads((repo / "data/locations.json").read_text(encoding="utf-8"))
eds = {k: v for k, v in locations.items() if not k.startswith("_") and v["type"] == "ed"}
roads = roads_mod.Roads(json.loads((work / "opendata/roads.geojson").read_text(encoding="utf-8"))["features"])
snapped = [roads.snap(r["lat"], r["lon"]) for r in real]
minutes = []
for speeds in roads_mod.MODELS:
    table = {}
    for ed_id, ed in eds.items():
        node, extra, _ = roads.snap(ed["lat"], ed["lon"])
        reach = roads.minutes_from(node, extra, speeds)
        table[ed_id] = [reach.get(n, math.inf) + walk for n, walk, _ in snapped]
    minutes.append(table)
# The site's seesEveryone(): the main answer must treat every age in the group.
eligible = {care: [k for k, v in eds.items() if (v["min_age"] == 0 if care == "child" else v["max_age"] is None and v["min_age"] <= 16)]
            for care in ("adult", "child")}
if not all(eligible.values()):
    sys.exit(f"no ED treats every age in a group: {eligible}")


def slower(i, ed_id, care):
    """Minutes slower than the quickest ED for that age, in the kindest speed model."""
    return min(m[ed_id][i] - min(m[k][i] for k in eligible[care]) for m in minutes)


# ---------- what the site lists ----------
FIRST_ROWS = """(names) => {
  const W = window.whichED, out = {};
  for (const q of names) {
    const p = W.findPlaces(q)[0];
    out[q] = p ? [p.label, p.names, W.results(p, 'adult').main.id, W.results(p, 'child').main.id, p.lat, p.lon] : null;
  }
  return out; }"""
with sc.sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", chromium_sandbox=True)
    site = sc.Site(browser)
    first = site.page.evaluate(FIRST_ROWS, sorted({r["typed"] for r in real} | set(CONTROLS)))
    outside, errors = list(site.outside), list(site.errors)
    site.close()
    browser.close()

bad, same_name_elsewhere, longer_name, no_match, off_road = [], set(), set(), set(), set()
for i, r in enumerate(real):
    row = first.get(r["typed"])
    if not row:
        no_match.add(r["typed"])
        continue
    label, names, adult, child, row_lat, row_lon = row
    for care, ed_id in (("adult", adult), ("child", child)):
        late = slower(i, ed_id, care)
        if not math.isfinite(late):  # not joined to the road map (an island, say): can't be measured
            off_road.add(r["typed"])
            continue
        if late <= SLOWER_MIN:
            continue
        typed = norm(r["typed"])
        if typed in names and roads_mod.map_km(r["lat"], r["lon"], row_lat, row_lon) > SAME_PLACE_KM:
            same_name_elsewhere.add(f"{r['typed']} -> {label}")
        elif typed not in names and any(n.startswith(typed) for n in names):
            longer_name.add(f"{r['typed']} -> {label}")
        else:
            bad.append(f"{r['typed']} ({care}) -> first row {label}: {round(late)}+ min slower by road")

controls = {name: (first.get(name) or ["No match"])[0] for name in CONTROLS}
wrong_controls = {name: got for name, got in controls.items() if got != CONTROLS[name]}
print(f"controls listing themselves first: {len(CONTROLS) - len(wrong_controls)} of {len(CONTROLS)} {wrong_controls or ''}")
print(f"real places {len(real)} | names searched {len(first)} | no match {len(no_match)} | outside requests {len(outside)} | page errors {len(errors)}")
print(f"not joined to the road map, so not measured: {len(off_road)} {sorted(off_road)[:10]}")
print(f"same name, a different place over {SAME_PLACE_KM} km away, listed first and over {SLOWER_MIN} min slower (allowed, county shown): {len(same_name_elsewhere)}")
for line in sorted(same_name_elsewhere):
    print("  allowed", line)
print(f"longer name that starts with it listed first, over {SLOWER_MIN} min slower (allowed): {len(longer_name)}")
for line in sorted(longer_name):
    print("  allowed", line)
print(f"any other first row over {SLOWER_MIN} min slower in every speed model: {len(bad)}")
for line in sorted(bad)[:40]:
    print("  FAIL", line)
sys.exit(1 if bad or outside or errors or wrong_controls else 0)
