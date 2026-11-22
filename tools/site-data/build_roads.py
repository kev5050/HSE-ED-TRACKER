"""Find towns where the closest ED on the map is much slower to drive to.

Reads WORKDIR/opendata/roads.geojson (Tailte Eireann Roads 250k, CC BY 4.0, no ferries),
data/locations.json and site/places.json. Writes WORKDIR/road-fix.json only. No network.

A town gets an override for an age group only if all of these hold:
  - the map-closest ED is at least MIN_SAVING minutes slower in the base speed model;
  - the same ED is quickest under every speed model;
  - the map-closest ED is at least MIN_EVERY_MODEL minutes slower under every model;
  - the town is within MAX_OFF_ROAD metres of a road in the map.
Usage: python build_roads.py WORKDIR REPO
"""
import collections
import heapq
import json
import math
import sys
from pathlib import Path

MIN_SAVING = 10
MIN_EVERY_MODEL = 5
MAX_OFF_ROAD = 2500
# km/h for motorway, national and regional roads. The first model is the base.
MODELS = [(100, 72, 52), (115, 88, 45), (85, 62, 56), (60, 60, 60)]
MOTORWAY, NATIONAL = 16, 14


def to_itm(lat, lon):
    """WGS84 degrees to Irish Transverse Mercator metres (EPSG:2157)."""
    a, f, k = 6378137.0, 1 / 298.257222101, 0.99982
    e2 = f * (2 - f)
    e = e2 / (1 - e2)

    def arc(p):
        return a * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * p
                    - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * p)
                    + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * p)
                    - 35 * e2**3 / 3072 * math.sin(6 * p))

    p = math.radians(lat)
    n = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    t, c = math.tan(p) ** 2, e * math.cos(p) ** 2
    d = math.cos(p) * math.radians(lon + 8)
    x = 600000 + k * n * (d + (1 - t + c) * d**3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * e) * d**5 / 120)
    y = 750000 + k * (arc(p) - arc(math.radians(53.5))
                      + n * math.tan(p) * (d * d / 2 + (5 - t + 9 * c + 4 * c * c) * d**4 / 24))
    return x, y


def map_km(a_lat, a_lon, b_lat, b_lon):
    return math.hypot((b_lat - a_lat) * 111.2, (b_lon - a_lon) * 111.2 * math.cos(math.radians((a_lat + b_lat) / 2)))


class Roads:
    """The road map as a graph, independent of speed."""

    def __init__(self, features):
        self.points, index, self.edges = [], {}, collections.defaultdict(list)

        def node(p):
            key = (round(p[0]), round(p[1]))
            if key not in index:
                index[key] = len(self.points)
                self.points.append(key)
            return index[key]

        ends = []
        for f in features:
            g, props = f["geometry"], f["properties"]
            if not g or props["FCsubtype"] != 1:  # subtype 2 includes the ferries
                continue
            for line in [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]:
                nodes = [node(p) for p in line]
                for a, b in zip(nodes, nodes[1:]):
                    self.link(a, b, props["RTT"])
                ends += [nodes[0], nodes[-1]]

        # roads that meet without sharing a vertex
        cells = collections.defaultdict(list)
        for i, (x, y) in enumerate(self.points):
            cells[(x // 500, y // 500)].append(i)
        for a in set(ends):
            x, y = self.points[a]
            for i in range(-1, 2):
                for j in range(-1, 2):
                    for b in cells.get((x // 500 + i, y // 500 + j), ()):
                        if math.dist(self.points[a], self.points[b]) <= 80:
                            self.link(a, b, "join")

        seen, largest = set(), []
        for start in range(len(self.points)):
            if start in seen:
                continue
            seen.add(start)
            part, todo = [], [start]
            while todo:
                u = todo.pop()
                part.append(u)
                for v, _, _ in self.edges[u]:
                    if v not in seen:
                        seen.add(v)
                        todo.append(v)
            largest = max(largest, part, key=len)
        self.cells = collections.defaultdict(list)
        for i in sorted(largest):
            self.cells[(self.points[i][0] // 3000, self.points[i][1] // 3000)].append(i)

    def link(self, a, b, kind):
        if a != b:
            km = math.dist(self.points[a], self.points[b]) / 1000
            self.edges[a].append((b, km, kind))
            self.edges[b].append((a, km, kind))

    def snap(self, lat, lon):
        """Nearest road vertex, minutes to reach it, and metres away."""
        p = to_itm(lat, lon)
        cx, cy = int(p[0] // 3000), int(p[1] // 3000)
        for r in (1, 2, 4, 8, 20):
            near = [i for x in range(cx - r, cx + r + 1) for y in range(cy - r, cy + r + 1) for i in self.cells.get((x, y), ())]
            if near:
                metres, i = min((math.dist(p, self.points[i]), i) for i in near)
                return i, metres * 1.3 / 1000 / 35 * 60, metres
        raise ValueError(f"no road near {lat},{lon}")

    def minutes_from(self, start, extra, speeds):
        motorway, national, regional = speeds
        best, queue = {start: extra}, [(extra, start)]
        while queue:
            so_far, u = heapq.heappop(queue)
            if so_far > best[u]:
                continue
            for v, km, kind in self.edges[u]:
                kmh = 40 if kind == "join" else motorway if kind == MOTORWAY else national if kind == NATIONAL else regional
                t = so_far + km / kmh * 60
                if t < best.get(v, float("inf")):
                    best[v] = t
                    heapq.heappush(queue, (t, v))
        return best


def main():
    work, repo = Path(sys.argv[1]), Path(sys.argv[2])
    roads = Roads(json.loads((work / "opendata/roads.geojson").read_text(encoding="utf-8"))["features"])
    sites = json.loads((repo / "data/locations.json").read_text(encoding="utf-8"))
    eds = {k: v for k, v in sites.items() if not k.startswith("_") and v["type"] == "ed"}
    places = json.loads((repo / "site/places.json").read_text(encoding="utf-8"))
    place_at = [roads.snap(p[3], p[4]) for p in places]

    minutes = []  # minutes[model][ed][place]
    for speeds in MODELS:
        table = {}
        for ed_id, ed in eds.items():
            node, extra, _ = roads.snap(ed["lat"], ed["lon"])
            reach = roads.minutes_from(node, extra, speeds)
            table[ed_id] = [reach[n] + walk for n, walk, _ in place_at]
        minutes.append(table)

    fixes = {}
    for i, p in enumerate(places):
        if place_at[i][2] > MAX_OFF_ROAD:
            continue
        for care in ("adult", "child"):
            can = [k for k, v in eds.items() if (v["min_age"] == 0 if care == "child" else v["max_age"] is None)]
            closest = min(can, key=lambda k: map_km(p[3], p[4], eds[k]["lat"], eds[k]["lon"]))
            quickest = [min(can, key=lambda k: m[k][i]) for m in minutes]
            saved = [m[closest][i] - m[quickest[0]][i] for m in minutes]
            if saved[0] >= MIN_SAVING and min(saved) >= MIN_EVERY_MODEL and len(set(quickest)) == 1:
                fixes.setdefault(i, {"name": p[0], "county": p[1]})[care] = quickest[0]
    (work / "road-fix.json").write_text(json.dumps(fixes, ensure_ascii=False, indent=1), encoding="utf-8")
    print("road overrides:", len(fixes), "towns")


if __name__ == "__main__":
    main()
