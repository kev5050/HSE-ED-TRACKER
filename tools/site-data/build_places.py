"""Build data/locations.json and site/places.json from fetch_sources.py's downloads.

Reads WORKDIR and the repo (for each site's short town label). No network.
Writes nothing if an age, map point or Eircode check fails.
Usage: python -X utf8 build_places.py WORKDIR REPO
"""
import collections
import html
import json
import math
import re
import struct
import sys
import unicodedata
from pathlib import Path

B, R = Path(sys.argv[1]), Path(sys.argv[2])


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


OLD = load(R / "data/locations.json")
PINS = load(B / "pins.json")["pins"]  # used only to check our points, never copied
OSM = {f"{e['type']}/{e['id']}": e.get("center", e) for e in load(B / "opendata/osm.json")["elements"]}
bad = []


def km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def norm(s):
    s = s.lower().replace("'", "").replace("’", "")
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def page_text(raw):
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S)
    return [x for x in (html.unescape(t).strip() for t in re.split(r"<[^>]+>", body)) if x]


# id | HSE page slug | OpenStreetMap element, or lat,lon where OSM has no matching element
ED_SUFFIX = "-emergency-department"
SITES = """beaumont-hospital beaumont-hospital way/22634348
chi-at-crumlin childrens-health-ireland-at-crumlin way/32063615
chi-at-tallaght childrens-health-ireland-at-tallaght way/51231573
chi-at-temple-street childrens-health-ireland-at-temple-street way/137349173
cavan-general-hospital cavan-general-hospital way/152613437
connolly-hospital connolly-hospital-blanchardstown way/12219521
cork-university-hospital cork-university-hospital way/118618667
galway-university-hospital university-hospital-galway way/26131060
letterkenny-university-hospital letterkenny-university-hospital relation/5589207
mrh-mullingar regional-hospital-mullingar way/49414502
mrh-portlaoise midland-regional-hospital-portlaoise way/223248196
mrh-tullamore midland-regional-hospital-tullamore way/56968562
mater-misericordiae-university-hospital mater-misericordiae-university-hospital way/33005181
mayo-university-hospital mayo-university-hospital way/444951067
mercy-university-hospital mercy-university-hospital way/353427032
naas-general-hospital naas-general-hospital way/353435239
our-lady-of-lourdes-hospital our-lady-of-lourdes-hospital-drogheda way/257323730
our-ladys-hospital-navan our-ladys-hospital-navan way/152006619
portiuncula-university-hospital portiuncula-university-hospital way/183750993
sligo-university-hospital sligo-university-hospital way/1533711209
st-lukes-general-hospital-kilkenny st-lukes-general-hospital-kilkenny way/120235458
st-jamess-hospital st-jamess-hospital-dublin way/32015912
st-vincents-university-hospital st-vincents-university-hospital relation/12144599
tallaght-university-hospital tallaght-university-hospital way/51231573
tipperary-university-hospital tipperary-university-hospital way/353442127
uh-kerry university-hospital-kerry way/111471269
uh-limerick university-hospital-limerick way/45766917
uh-waterford university-hospital-waterford way/32152329
wexford-general-hospital wexford-general-hospital way/353438490
iu-ballina ballina-injury-unit way/473024122
iu-bantry bantry-local-injury-unit way/177777682
iu-cashel cashel-injury-unit-tipperary way/381225757
iu-dundalk dundalk-injury-unit relation/5274433
iu-ennis ennis-injury-unit way/353440320
iu-loughlinstown st-columcilles-injury-unit-loughlinstown way/51680291
iu-mallow mallow-injury-unit way/177571700
iu-mercy the-mercy-injury-unit way/75918606
iu-monaghan monaghan-injury-unit 54.2498,-6.9741
iu-mullingar mullingar-minor-injuries-unit way/49404615
iu-naas naas-hospital-injury-unit way/353435239
iu-nenagh nenagh-injury-unit way/36393501
iu-roscommon roscommon-injury-unit way/196744297
iu-smithfield mater-smithfield-rapid-injury-clinic 53.3480,-6.2780
iu-st-johns st-johns-injury-unit-limerick way/353433454"""

# ---------- Eircode routing areas (Autoaddress shapefile) ----------
sd = B / "autoaddr/shp"
dbf = (sd / "RoutingKeys_WGS84_region.dbf").read_bytes()
count, header_len, rec_len = struct.unpack("<IHH", dbf[4:12])
widths, i = [], 32
while dbf[i] != 13:
    widths.append(dbf[i + 16])
    i += 32
attrs = []
for r in range(count):
    p, values = header_len + r * rec_len + 1, []
    for w in widths:
        values.append(dbf[p:p + w].decode("latin-1").strip())
        p += w
    attrs.append(values)

shp = (sd / "RoutingKeys_WGS84_region.shp").read_bytes()
p, POLY = 100, {}
for a in attrs:
    size = struct.unpack(">i", shp[p + 4:p + 8])[0] * 2
    c = shp[p + 8:p + 8 + size]
    p += 8 + size
    box = struct.unpack("<4d", c[4:36])
    n_parts, n_points = struct.unpack("<2i", c[36:44])
    parts = list(struct.unpack(f"<{n_parts}i", c[44:44 + 4 * n_parts])) + [n_points]
    start = 44 + 4 * n_parts
    xy = struct.unpack(f"<{2 * n_points}d", c[start:start + 16 * n_points])
    pts = list(zip(xy[::2], xy[1::2]))
    key = next(x for x in a if re.fullmatch(r"[A-Z]\d[0-9W]", x))
    desc = next(x for x in a if x != key and re.search(r"[A-Za-z]{3}", x))
    POLY[key] = {"box": box, "desc": desc, "rings": [pts[parts[j]:parts[j + 1]] for j in range(n_parts)]}


def crossings(poly, lat):
    for ring in poly["rings"]:
        for (ax, ay), (bx, by) in zip(ring, ring[1:]):
            if (ay > lat) != (by > lat):
                yield ax + (lat - ay) * (bx - ax) / (by - ay)


def inside(poly, lat, lon):
    x0, y0, x1, y1 = poly["box"]
    if not (x0 <= lon <= x1 and y0 <= lat <= y1):
        return False
    return sum(lon < x for x in crossings(poly, lat)) % 2 == 1


def key_of(lat, lon):
    return next((k for k, poly in POLY.items() if inside(poly, lat, lon)), "")


def middle(poly):
    ring = max(poly["rings"], key=lambda r: abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:]))))
    ox, oy = ring[0]
    total = cx = cy = 0
    for (a, b), (c, d) in zip(ring, ring[1:]):
        a, b, c, d = a - ox, b - oy, c - ox, d - oy
        f = a * d - c * b
        total += f
        cx += (a + c) * f
        cy += (b + d) * f
    lat, lon = oy + cy / (3 * total), ox + cx / (3 * total)
    if not inside(poly, lat, lon):
        xs = sorted(crossings(poly, lat))
        left, right = max(zip(xs[::2], xs[1::2]), key=lambda v: v[1] - v[0])
        lon = (left + right) / 2
    return lat, lon


# ---------- sites ----------
sites, gaps, outside = [], [], []
for line in SITES.split("\n"):
    sid, slug, point = line.split()
    iu = sid.startswith("iu-")
    if not iu:
        slug += ED_SUFFIX
    raw = (B / "hse-pages" / f"{slug}.html").read_text(encoding="utf-8")
    text = page_text(raw)
    title = html.unescape(re.sub(r"<[^>]+>", "", re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S).group(1))).strip()
    address = re.sub(r"\s+", " ", text[text.index("Address") + 1])
    age = text[text.index("Age") + 1]
    older = re.search(r"age (\d+) and older", age)
    younger = re.search(r"age (\d+) and younger", age)
    if not (older or younger or "all ages" in age):
        bad.append(f"age unreadable: {sid}")
    if "/" in point:
        lat, lon = OSM[point]["lat"], OSM[point]["lon"]
    else:
        lat, lon = map(float, point.split(","))
    lat, lon = round(lat, 4), round(lon, 4)
    if title in PINS:
        gap = km(lat, lon, *PINS[title]["point"])
        gaps.append((round(gap, 2), sid))
        if gap > 0.5:
            bad.append(f"{sid} is {gap:.2f} km from the HSE pin")
    code = re.search(r"([A-Z]\d[0-9W]) ([0-9A-Z]{4})$", address)
    if not code:
        bad.append(f"no Eircode: {sid}")
        continue
    if key_of(lat, lon) != code.group(1):
        outside.append((sid, code.group(1), key_of(lat, lon)))

    rec = {
        "type": "iu" if iu else "ed",
        "name": re.sub(r" Emergency Department$", "", title).replace("’", "'"),
        "town": OLD[sid]["town"],
        "lat": lat, "lon": lon, "eircode": code.group(0),
        "min_age": int(older.group(1)) if older else 0,
        "max_age": int(younger.group(1)) if younger else None,
    }
    if younger:
        rec["children"] = True
    flight = "".join(json.loads('"' + m + '"') for m in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', raw, re.S))
    marker = '"openingHours":['
    listed = json.JSONDecoder().raw_decode(flight, flight.index(marker) + len(marker) - 1)[0]
    by_day = {x["day"]: [x["opening_hour"][:5], x["closing_hour"][:5]] for x in listed}
    hours = [by_day.get(d) for d in range(1, 8)]
    if iu:
        rec["hours"] = hours  # Monday first; null = closed
    elif hours != [["00:00", "23:59"]] * 7 and sid != "chi-at-tallaght":
        # CHI's own page says Tallaght is open 24 hours, 7 days a week. The HSE field is left over from a September closure.
        bad.append(f"ED not open all week: {sid}")
    rec["url"] = f"https://www2.hse.ie/services/find-urgent-emergency-care/{slug}/"
    sites.append((sid, rec))

# ---------- places ----------
places = []
for f in sorted(load(B / "opendata/urban.json")["features"], key=lambda f: -f["attributes"]["Shape__Area"]):
    a, c = f["attributes"], f["centroid"]
    name = a["URBAN_AREA_NAME"].strip()
    if name == "Dublin city and suburbs":
        continue  # too big for one point: Dublin uses its districts and suburbs
    places.append([re.sub(r" and suburbs$", "", name), a["COUNTY"].strip().title(), key_of(c["y"], c["x"]), round(c["y"], 4), round(c["x"], 4)])
towns = len(places)

# coastal town centres can sit just outside the coarse boundaries: use the closest area
for pl in places:
    if not pl[2]:
        pl[2] = min(POLY, key=lambda k: min(km(pl[3], pl[4], la, lo) for r in POLY[k]["rings"] for lo, la in r[::4]))

# CSO uses Irish names in the Gaeltacht: add the English name so either can be typed
OSM_PLACES = load(B / "opendata/suburbs.json")["elements"]
also = []
for pl in places:
    plain = norm(pl[0])
    for e in OSM_PLACES:
        if abs(e["lat"] - pl[3]) > 0.04 or abs(e["lon"] - pl[4]) > 0.06:
            continue
        t = e["tags"]
        english = t.get("name:en") or t["name"]
        same = plain in {norm(t.get(k, "")) for k in ("name", "name:ga", "official_name", "alt_name")}
        close = km(e["lat"], e["lon"], pl[3], pl[4]) < 1.2
        irish = any(ord(c) > 127 for c in pl[0]) or re.match(r"(An|Na|Béal|Gob|Baile|Cill|Doire|Gort|Anagaire|Carna|Gleann|Machaire) ", pl[0])
        if norm(english) != plain and norm(english).replace(" ", "") != plain.replace(" ", "") and (same or (close and irish)):
            also.append((pl[0], english))
            if plain == "gleann cholm cille":
                english = "Glencolumbkille"  # the nearest map label is a different village, Cashel
            pl[0] = f"{english} ({pl[0]})"
            break
print("English names added", len(also), also)

CITIES = [
    # Keys in that county only, because these places take the city's county. K78 Lucan, P31 Ballincollig, T45 Glanmire.
    ("Dublin", 53.3498, -6.2603, 17, lambda k: k.startswith("D") or k in ("A94", "A96", "K78")),
    ("Cork", 51.8985, -8.4756, 9, lambda k: k in ("T12", "T23", "P31", "T45")),
    ("Galway", 53.2707, -9.0568, 6, lambda k: k == "H91"),
    ("Limerick", 52.6638, -8.6267, 6, lambda k: k == "V94"),
    ("Waterford", 52.2593, -7.1101, 6, lambda k: k == "X91"),
]
have = {(norm(p[0]), p[1]) for p in places}
rank = ["town", "suburb", "village"]
for e in sorted(load(B / "opendata/suburbs.json")["elements"], key=lambda e: rank.index(e["tags"]["place"])):
    for county, lat, lon, radius, in_city in CITIES:
        if km(lat, lon, e["lat"], e["lon"]) > radius:
            continue
        key = key_of(e["lat"], e["lon"])
        name = e["tags"].get("name:en") or e["tags"]["name"]
        if in_city(key) and (norm(name), county) not in have:
            have.add((norm(name), county))
            places.append([name, county, key, round(e["lat"], 4), round(e["lon"], 4)])
suburbs = len(places) - towns

with_place = {p[2] for p in places}
area_rows = []
for key, poly in POLY.items():
    if key.startswith("D") or key in ("A94", "A96", "T12", "T23") or key not in with_place:
        lat, lon = middle(poly)
        name = re.sub(r"(\d)w\b", r"\1W", poly["desc"].title(), flags=re.I)
        name = {"T12": "Cork city south", "T23": "Cork city north"}.get(key, name)
        name = {"P32": "Donoughmore"}.get(key, name)
        if not re.search(r"\d|city", name):
            name += " area"
        county = "Dublin" if key.startswith("D") or key in ("A94", "A96", "K78") else "Cork" if key[0] in "TP" else ""
        area_rows.append([name, county, key, round(lat, 4), round(lon, 4)])
# D01, D02 ... D24, with D6W straight after D06
area_rows.sort(key=lambda p: (not p[2].startswith("D"), int(re.sub(r"\D", "", p[2])), p[2]))
places += area_rows

if len(POLY) != 139:
    bad.append(f"{len(POLY)} routing keys, expected 139")
if set(POLY) - {p[2] for p in places}:
    bad.append(f"routing keys with no place: {set(POLY) - {p[2] for p in places}}")
if len(sites) != 44:
    bad.append(f"{len(sites)} sites, expected 44")
# The day fetch_sources.py read the HSE pages, which is when the ages and hours were last known true.
read_on = B / "hse-pages-read-on.txt"
checked = read_on.read_text(encoding="utf-8").strip() if read_on.is_file() else ""
if not re.fullmatch(r"\d{4}-\d\d-\d\d", checked):
    bad.append("no date in hse-pages-read-on.txt: run fetch_sources.py")
if len(gaps) < 40:
    bad.append(f"only {len(gaps)} sites could be checked against an HSE map pin")  # e.g. pins.json came back empty

# ---------- checks against official pages ----------
ok = total = 0
misses = set()
for f in (B / "hse-pages").glob("[!_]*.html"):
    text = page_text(f.read_text(encoding="utf-8"))
    if "Address" not in text:
        continue
    address = text[text.index("Address") + 1]
    m = re.search(r"\b([A-Z]\d[0-9W])\s*[0-9A-Z]{4}$", address)
    if not m:
        continue
    total += 1
    if any(norm(p[0].split(" (")[0]).replace(" city", "").replace(" ", "") in norm(address).replace(" ", "") for p in places if p[2] == m.group(1)):
        ok += 1
    else:
        misses.add(f"{m.group(1)} {address[:90]}")

print("sites", len(sites), "| towns", towns, "| suburbs", suburbs, "| areas", len(area_rows))
print("largest pin gaps", sorted(gaps, reverse=True)[:6])
print("site point outside its Eircode area", outside)
print(f"HSE addresses whose town is listed under that key: {ok}/{total}")
for m in sorted(misses):
    print("  miss", m)
print("no key", [p[:2] for p in places if not p[2]])
print("no county", [p[:3] for p in places if not p[1]])
print("counties", sorted({p[1] for p in places}))
print("areas", [p[0] for p in area_rows])
print("same name and county", [k for k, v in collections.Counter((p[0], p[1]) for p in places).items() if v > 1])

if bad:
    print("NOT WRITTEN", bad)
    sys.exit(1)

note = ("Every ED and HSE Injury Unit. Ages, Eircodes and Injury Unit hours come from each site's HSE page (url). "
        "Map points are from OpenStreetMap (ODbL). See docs/site-data.md before editing. 'children' = children's ED only.")
lines = ["{", f'  "_note": {json.dumps(note)},', f'  "_checked": "{checked}",']
lines += [f"  {json.dumps(s)}: {json.dumps(r, ensure_ascii=False)}" + ("," if j < len(sites) - 1 else "") for j, (s, r) in enumerate(sites)]
lines.append("}")
(R / "data/locations.json").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
SPELLING = {
    "Dingle (Dingle-Daingean Uí Chuis)": "Dingle (Daingean Uí Chúis)",
    "Cahirciveen": "Cahersiveen (Cahirciveen)",  # OpenStreetMap's spelling first
    "Miltown Malbay": "Miltown Malbay (Milltown Malbay)",  # CSO name first, OpenStreetMap's spelling as the alias
}
for pl in places:
    pl[0] = SPELLING.get(pl[0], pl[0])
rows = ",\n".join(json.dumps(p, ensure_ascii=False, separators=(",", ":")) for p in places)
(R / "site/places.json").write_text(f"[\n{rows}\n]\n", encoding="utf-8", newline="\n")
print("written")
