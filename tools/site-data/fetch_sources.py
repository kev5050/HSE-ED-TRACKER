"""Download the public sources the site's data is built from.

Network: GET or query only, to www2.hse.ie, the Google Maps links HSE publishes (read for
their point, used only to check ours), data-osi.opendata.arcgis.com and arcgis.com
(Tailte Eireann and CSO), overpass-api.de (OpenStreetMap) and Autoaddress's file host.
Sends no personal data. Writes only into WORKDIR, which should be a new empty folder.
Usage: python -I fetch_sources.py WORKDIR
"""
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HSE = "https://www2.hse.ie/services/find-urgent-emergency-care/"
URBAN = ("https://services-eu1.arcgis.com/BuS9rtTsYEV5C0xh/arcgis/rest/services/"
         "Urban_Areas_National_Statistical_Boundaries_2022_Generalised_20m/FeatureServer/5/query"
         "?where=1%3D1&outFields=*&returnGeometry=false&returnCentroid=true&outSR=4326&f=json")
ROADS = "https://data-osi.opendata.arcgis.com/api/download/v1/items/61c59828f60e471095f9945bb59a69e1/geojson?layers=0"
AREAS = ("https://s3.amazonaws.com/helpscout.net/docs/assets/6344286c4bb0327cee7a64d7/attachments/"
         "63f73f073c396c395ec0eea7/routingkeys_shape_wgs84_2016_09_29.zip")
OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = "(51.4,-10.6,55.4,-5.9)"
QUERIES = {
    "osm.json": f'[out:json][timeout:90];nwr["amenity"="hospital"]{BBOX};out center tags;',
    "suburbs.json": f'[out:json][timeout:90];node["place"~"^(suburb|town|village)$"]["name"]{BBOX};out;',
}
AGENT = {"User-Agent": "Mozilla/5.0"}
PIN_HOSTS = {"maps.app.goo.gl", "www.google.com", "maps.google.com"}


def get(url, data=None, agent=AGENT):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=agent), timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(15 * (attempt + 1))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def pin_point(url):
    opener = urllib.request.build_opener(NoRedirect)
    for _ in range(3):
        m = re.search(r"!3d(5\d\.\d+)!4d(-\d+\.\d+)", url)
        if m:
            return float(m.group(1)), float(m.group(2))
        to = urllib.parse.urlsplit(url)
        if to.scheme != "https" or to.hostname not in PIN_HOSTS:
            return None  # never follow a redirect to a file, plain http or another host
        try:
            opener.open(urllib.request.Request(url, headers=AGENT), timeout=20)
            return None
        except urllib.error.HTTPError as e:
            if not e.headers.get("Location"):
                return None
            url = e.headers["Location"]
    return None


def main():
    work = Path(sys.argv[1])
    pages, opendata, areas = work / "hse-pages", work / "opendata", work / "autoaddr" / "shp"
    for d in (pages, opendata, areas):
        d.mkdir(parents=True, exist_ok=True)

    slugs = set()
    first = get(HSE + "?page=1").decode("utf-8")
    last = max(int(n) for n in re.findall(r"[?]page=(\d+)", first))
    for n in range(0, last + 1):
        listing = get(f"{HSE}?page={n}").decode("utf-8")
        slugs.update(re.findall(r'href="/services/find-urgent-emergency-care/([a-z0-9-]+)/"', listing))
        time.sleep(0.4)

    pins = {}
    for slug in sorted(slugs):
        raw = get(f"{HSE}{slug}/")
        (pages / f"{slug}.html").write_bytes(raw)
        page = raw.decode("utf-8")
        if re.search(r"(emergency department|injury unit) is for", page):
            title = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1)
            link = re.search(r"https://maps\.app\.goo\.gl/[A-Za-z0-9]+", page)
            point = pin_point(link.group(0)) if link else None
            if point:
                pins[html.unescape(re.sub(r"<[^>]+>", "", title)).strip()] = {"point": point}
        time.sleep(0.4)
    (work / "pins.json").write_text(json.dumps({"pins": pins}, indent=1), encoding="utf-8")
    # The day the HSE pages were read. build_places.py puts it in locations.json and the site's footer.
    (work / "hse-pages-read-on.txt").write_text(time.strftime("%Y-%m-%d") + "\n", encoding="utf-8")

    (opendata / "urban.json").write_bytes(get(URBAN))
    (opendata / "roads.geojson").write_bytes(get(ROADS))
    for name, query in QUERIES.items():
        (opendata / name).write_bytes(get(OVERPASS, urllib.parse.urlencode({"data": query}).encode(), {"User-Agent": "curl"}))

    with zipfile.ZipFile(io.BytesIO(get(AREAS))) as z:
        for ext in ("dbf", "shp"):
            name = f"RoutingKeys_WGS84_region.{ext}"
            (areas / name).write_bytes(z.read(name))
    print("HSE pages", len(slugs), "| pins", len(pins))


if __name__ == "__main__":
    main()
