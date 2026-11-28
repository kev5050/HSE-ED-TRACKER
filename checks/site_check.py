"""End-to-end check of the whichED site in real Chrome.

Reads site/ and data/ and serves them to a sandboxed system Chrome on a pretend
address. Every other request is blocked and counted, so nothing reaches the
network. Writes screenshots to OUT_DIR and nothing else. Exits 1 on any failure.

Kept out of tests/ on purpose: the hourly fetch job runs pytest and has no browser.
Needs: pip install playwright (uses installed Chrome).
Usage: python checks/site_check.py OUT_DIR [REPO_DIR] [case,case,...]
"""
import json
import mimetypes
import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
ROOT = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent).resolve()
ONLY = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else set()
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://site.test"
DUBLIN = ZoneInfo("Europe/Dublin")
MONDAY_10AM = datetime(2026, 11, 23, 10, 0, tzinfo=DUBLIN)
MAX_TAPS = 3
DROGHEDA = "Our Lady of Lourdes Hospital Drogheda"

# Typed from the HSE's own pages on 22 Nov 2026, separately from data/locations.json,
# so a wrong edit to that file fails here. Value: (youngest, oldest) age seen.
HSE_AGES = {
    "beaumont-hospital": (16, None), "connolly-hospital": (16, None),
    "mater-misericordiae-university-hospital": (16, None), "st-jamess-hospital": (16, None),
    "tallaght-university-hospital": (16, None), "mercy-university-hospital": (16, None),
    "naas-general-hospital": (16, None), "our-ladys-hospital-navan": (16, None),
    "st-vincents-university-hospital": (14, None),
    "chi-at-crumlin": (0, 15), "chi-at-tallaght": (0, 15), "chi-at-temple-street": (0, 15),
    **{h: (0, None) for h in (
        "cavan-general-hospital", "cork-university-hospital", "galway-university-hospital",
        "letterkenny-university-hospital", "mrh-mullingar", "mrh-portlaoise", "mrh-tullamore",
        "mayo-university-hospital", "our-lady-of-lourdes-hospital", "portiuncula-university-hospital",
        "sligo-university-hospital", "st-lukes-general-hospital-kilkenny", "tipperary-university-hospital",
        "uh-kerry", "uh-limerick", "uh-waterford", "wexford-general-hospital")},
    **{f"iu-{u}": (5, None) for u in (
        "ballina", "bantry", "cashel", "dundalk", "ennis", "mallow", "monaghan", "nenagh", "roscommon", "st-johns")},
    "iu-mercy": (10, None), "iu-loughlinstown": (14, None),
    "iu-mullingar": (16, None), "iu-naas": (16, None), "iu-smithfield": (16, None),
}

# Counts calls, so a check can prove the page never asks where someone is.
GEO_SPY = """(() => {
  window.__geoCalls = 0;
  if (!navigator.geolocation) return;
  for (const f of ["getCurrentPosition", "watchPosition"]) navigator.geolocation[f] = () => { window.__geoCalls++; };
})()"""

MEASURE = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && !el.closest('.sr'); };
  const parse = (c) => {
    let m = c.match(/rgba?\(([^)]+)\)/), k = 1;
    if (!m) { m = c.match(/color\(srgb ([^)]+)\)/); k = 255; }
    if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return [p[0] * k, p[1] * k, p[2] * k, p.length > 3 ? p[3] : 1]; };
  const blend = (t, b) => [0, 1, 2].map((i) => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
  const bgOf = (el) => { const s = [];
    for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c[3] > 0) { s.push(c); if (c[3] >= 1) break; } }
    return s.reverse().reduce((under, c) => blend(c, under), [255, 255, 255, 1]); };
  const lum = (c) => c.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  const out = { overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, small: [], contrast: [], targets: [], noWord: 0, texts: 0 };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), seen = new Set();
  while (w.nextNode()) {
    const el = w.currentNode.parentElement, text = w.currentNode.textContent.trim();
    if (!text || seen.has(el) || !vis(el)) continue;
    seen.add(el); out.texts++;
    const cs = getComputedStyle(el), size = parseFloat(cs.fontSize), bg = bgOf(el), fg = blend(parse(cs.color), bg);
    const a = lum(fg), b = lum(bg), ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (size < 15.95) out.small.push(text.slice(0, 40));
    if (ratio < ((size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700)) ? 3 : 4.5)) out.contrast.push(ratio.toFixed(2) + ' ' + text.slice(0, 40));
  }
  for (const el of document.querySelectorAll('a,button,input,summary')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 43.5 || r.width < 43.5) out.targets.push(Math.round(r.width) + 'x' + Math.round(r.height) + ' ' + (el.textContent || el.id).trim().slice(0, 30));
  }
  out.noWord = [...document.querySelectorAll('.status')].filter((s) => vis(s) && !/[A-Za-z]/.test(s.textContent)).length;
  return out;
}
"""


def need(condition, message):
    if not condition:
        raise AssertionError(message)


def report(day="2026-11-23", time="08:00", counts=None, zeros=()):
    """A made-up report and history. Every ED has 10 waiting and usually 10 unless counts says otherwise."""
    counts, hospitals, history = counts or {}, [], {}
    for hid in HSE_AGES:
        if hid.startswith("iu-"):
            continue
        today, usual = (0, 0) if hid in zeros else counts.get(hid, (10, 10))
        hospitals.append({"id": hid, "name": hid, "has_ed": True, "total_trolleys": today,
                          "usual_total_trolleys": usual, "baseline_days": 16})
        history[hid] = [[f"2026-11-{d:02d}", 0 if hid in zeros else max(1, round(usual or 1))] for d in range(7, 23)]
    return {"latest.json": {"report_date": day, "snapshot_time": time, "hospitals": hospitals},
            "history.json": {"national": [], "hospitals": history}}


def real_places():
    return json.loads((ROOT / "site/places.json").read_text(encoding="utf-8"))


def real_locations():
    return json.loads((ROOT / "data/locations.json").read_text(encoding="utf-8"))


class Site:
    """One fresh browser tab showing the site.

    files replaces data/ files and site replaces site/ files: a value is the body, None for a
    failed request, or "hold" to keep the request waiting until release()."""

    def __init__(self, browser, files=None, site=None, now=MONDAY_10AM, width=360, height=780, scheme="light",
                 zone="Europe/Dublin", js=True, clock="fixed", errors_expected=False):
        self.files = report() if files is None else files
        self.site = site or {}
        self.errors_expected = errors_expected
        self.asked, self.outside, self.errors, self.held = [], [], [], []
        self.ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                       timezone_id=zone, java_script_enabled=js)
        self.ctx.set_default_timeout(4000)
        self.ctx.route("**/*", self.serve)
        self.page = self.ctx.new_page()
        if js:
            self.page.add_init_script(GEO_SPY)
            if clock == "install":
                self.page.clock.install(time=now)
            else:
                self.page.clock.set_fixed_time(now)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and self.errors.append(m.text))
        self.page.goto(BASE + "/")
        if "hold" in [*self.files.values(), *self.site.values()]:
            self.page.wait_for_timeout(1000)
        else:
            self.page.wait_for_load_state("networkidle")

    def serve(self, route):
        url = route.request.url
        if not url.startswith(BASE + "/"):
            self.outside.append(url)
            return route.abort()
        self.asked.append(url)
        path = unquote(urlsplit(url).path).lstrip("/") or "index.html"
        if path.startswith("data/"):
            name, swaps, file = path[5:], self.files, ROOT / "data" / path[5:]
        else:
            name, swaps, file = path, self.site, ROOT / "site" / path
        if name in swaps:
            body = swaps[name]
            if body == "hold":
                self.held.append((route, file))
                return None
            if body is None:
                return route.fulfill(status=503, body="")
            if isinstance(body, str):
                return route.fulfill(content_type=mimetypes.guess_type(name)[0] or "text/plain", body=body)
            return route.fulfill(content_type="application/json", body=json.dumps(body))
        if not file.is_file():
            return route.fulfill(status=404, body="")
        return route.fulfill(content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream", body=file.read_bytes())

    def release(self):
        """Let held requests finish with the real file."""
        while self.held:
            route, file = self.held.pop()
            route.fulfill(content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream", body=file.read_bytes())

    def requests(self, path):
        return sum(1 for u in self.asked if urlsplit(u).path == "/" + path)

    def care(self, value):
        button = self.page.locator(f"#care button[data-care={value}]")
        button.click()
        need(button.get_attribute("aria-checked") == "true", f"{value} did not switch on")

    def place(self, typed, option=None):
        self.page.fill("#where", "")
        self.page.click("#where")
        self.page.keyboard.type(typed)
        options = self.page.locator("#suggest button")
        need(options.count() and self.page.locator("#suggest").is_visible(), f"nothing to tap for {typed}")
        (options.filter(has_text=option).first if option else options.first).click()
        need(not self.page.locator("#suggest").is_visible(), f"list still open after choosing {typed}")

    def go(self, typed, care, option=None):
        self.care(care)
        self.place(typed, option)
        need(self.page.locator("#answer").is_visible(), f"no answer for {typed} / {care}")
        shown = self.page.inner_text("#answer") + self.page.inner_text("#more")
        need(not re.search(r"null|undefined|NaN", shown), f"programming words on screen for {typed}: {shown[:160]}")
        return self.answer()

    def answer(self):
        return self.page.locator("#answer h2").inner_text()

    def others(self):
        return self.page.locator("#options .name").all_inner_texts()

    def text(self):
        return self.page.inner_text("body")

    def focused(self):
        return self.page.evaluate("document.activeElement.id || document.activeElement.getAttribute('aria-label') || document.activeElement.tagName")

    def come_back(self):
        """What the browser does when someone returns to the tab."""
        self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    def close(self):
        for route, _ in self.held:
            route.abort()
        self.ctx.close()


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def navan_parent_gets_drogheda_in_three_taps(new):
    """User story: a parent in Navan with a 6-year-old who needs emergency care."""
    s = new()
    taps = 0
    child = s.page.locator("#care button[data-care=child]")
    child.click()
    taps += 1
    need(child.get_attribute("aria-checked") == "true", "Child did not switch on")
    need(s.focused() == "where", f"choosing Child left focus on {s.focused()}, not the town box")
    s.page.keyboard.type("Navan")
    need(s.page.input_value("#where") == "Navan", "the town was not typed")
    need(not s.page.locator("#answer").is_visible(), "an answer appeared before a town was tapped")
    s.page.locator("#suggest button", has_text="Navan, Co. Meath").first.click()
    taps += 1
    need(s.page.locator("#answer").is_visible(), "no answer after tapping the town")
    need(s.answer() == DROGHEDA, f"got {s.answer()}")
    need(not any("Navan" in n for n in s.others()), "Navan hospital is listed for a child")
    link = s.page.locator("#answer a.directions")
    expected = link.get_attribute("href")
    need("A92%20VW28" in expected, "Directions don't go to Drogheda's Eircode")
    with s.page.expect_popup() as popup:
        link.click()
        taps += 1
    for _ in range(30):
        if s.outside:
            break
        s.page.wait_for_timeout(100)
    need(s.outside and s.outside[0] == expected, "Directions did not open")
    popup.value.close()
    need(taps <= MAX_TAPS, f"{taps} taps")


@case
def known_bad_towns_are_fixed_for_children(new):
    for typed, option, expected in (
        ("Naas", "Naas, Co. Kildare", "Children's Health Ireland at Tallaght"),
        ("Cork city", "Cork city, Co. Cork", "Cork University Hospital"),
        ("D12", "Dublin 12", "Children's Health Ireland at Crumlin"),
        ("D01", "Dublin 1", "Children's Health Ireland at Temple Street"),
    ):
        got = new().go(typed, "child", option)
        need(got == expected, f"{typed}: expected {expected}, got {got}")


@case
def adult_in_navan_still_gets_navan(new):
    s = new()
    need(s.go("Navan", "adult", "Navan, Co. Meath") == "Our Lady's Hospital Navan", s.answer())
    need(not any("Children" in n for n in s.others()), "a children's hospital is listed for an adult")


@case
def every_place_follows_the_hse_age_rules(new):
    s = new()
    rows = s.page.evaluate("""() => {
      const W = window.whichED, out = [];
      for (const p of W.places) for (const care of ['adult', 'child']) {
        const r = W.results(p, care);
        out.push({ place: p.name, care, main: r.main.id, others: r.others.map(x => x.id) });
      }
      return out; }""")
    need(len(rows) > 1800, f"only {len(rows)} combinations ran")
    for r in rows:
        low, high = HSE_AGES[r["main"]]
        ok = low == 0 if r["care"] == "child" else high is None
        need(ok and not r["main"].startswith("iu-"), f"main answer {r}")
        for other in r["others"]:
            low, high = HSE_AGES[other]
            need(low <= 15 if r["care"] == "child" else high is None, f"listed {other} in {r}")


@case
def road_rule_never_breaks_the_age_rules(new):
    rows = real_places()
    i = next(i for i, r in enumerate(rows) if r[:2] == ["Navan", "Meath"])
    rows[i] = rows[i][:5] + [{"adult": "our-ladys-hospital-navan", "child": "our-ladys-hospital-navan"}]
    s = new(site={"places.json": rows})
    need(s.go("Navan", "child", "Navan, Co. Meath") == DROGHEDA, f"a road rule sent a child to {s.answer()}")


@case
def data_matches_hse_ages(new):
    sites = {k: v for k, v in real_locations().items() if not k.startswith("_")}
    need(set(sites) == set(HSE_AGES), f"sites differ: {set(sites) ^ set(HSE_AGES)}")
    for sid, ages in HSE_AGES.items():
        need((sites[sid]["min_age"], sites[sid]["max_age"]) == ages, f"{sid} has the wrong ages")
        need(sites[sid]["url"].startswith("https://www2.hse.ie/"), f"{sid} has no HSE source")


@case
def every_eircode_area_finds_a_place(new):
    keys = new().page.evaluate("""() => {
      const W = window.whichED, keys = [...new Set(W.places.map(p => p.key))];
      return { count: keys.length, empty: keys.filter(k => !W.findPlaces(k).length) }; }""")
    need(keys == {"count": 139, "empty": []}, str(keys))


@case
def no_answer_until_age_and_place_are_chosen(new):
    s = new()
    need(s.page.locator("#care button[aria-checked=true]").count() == 0, "an age is preselected")
    s.place("Navan", "Navan, Co. Meath")
    need(not s.page.locator("#answer").is_visible(), "answer shown before Adult or Child")
    s.care("child")
    need(s.page.locator("#answer").is_visible(), "no answer once both are chosen")


@case
def changing_the_town_clears_the_old_answer(new):
    s = new()
    s.go("Navan", "child", "Navan, Co. Meath")
    s.page.fill("#where", "Gal")
    need(s.page.input_value("#where") == "Gal", "the new town was not typed")
    need(not s.page.locator("#answer").is_visible(), "old answer left showing")


@case
def adult_and_child_are_explained(new):
    text = new().text()
    for words in ("16 or older", "15 or younger", "don't treat children", "age limit"):
        need(words in text, f"missing: {words}")
    need("only show places that can see them" not in text, "promises to hide places a young child can't use")


@case
def other_age_limits_are_shown(new):
    s = new()
    s.go("D04", "child", "Dublin 4")
    row = s.page.locator("#options li", has_text="St Vincent's University Hospital")
    need(row.count() == 1, "St Vincent's not listed for older children")
    need("Age 14 and over only" in row.inner_text(), "no age 14 label")
    s = new()
    s.go("Mallow", "child", "Mallow, Co. Cork")
    need("Age 5 and over only" in s.page.locator("#options li", has_text="Mallow Injury Unit").inner_text(), "no age 5 label")


@case
def full_eircode_is_cut_to_three_and_never_sent(new):
    s = new()
    s.care("child")
    before = len(s.asked)
    s.page.click("#where")
    s.page.keyboard.type("C15 RK7Y")
    need(s.page.input_value("#where") == "C15", f"box kept {s.page.input_value('#where')}")
    options = s.page.locator("#suggest button").all_inner_texts()
    need(any("Navan" in o for o in options) and any("Trim" in o for o in options), str(options))
    s.page.locator("#suggest button", has_text="Navan").first.click()
    need("near Navan" in s.page.inner_text("#answer"), "doesn't say near Navan")
    need(len(s.asked) == before and not s.outside, "a request was made after typing")
    need(s.page.evaluate("localStorage.length + sessionStorage.length + document.cookie.length") == 0, "something was stored")


@case
def irish_and_english_town_names_work(new):
    for typed, shown in (("Dingle", "Dingle"), ("Beal an Mhuirthead", "Belmullet"), ("dun laoghaire", "Laoghaire"), ("Blanchardstown", "Blanchardstown")):
        s = new()
        found = s.page.evaluate("(q) => window.whichED.findPlaces(q).map(p => p.label)", typed)
        need(any(shown in f for f in found), f"{typed}: {found[:4]}")


@case
def big_places_can_be_found(new):
    page = new().page
    for typed, wanted in (
        ("Ballincollig", "Ballincollig, Co. Cork"), ("Glanmire", "Glanmire, Co. Cork"),
        ("Adamstown", "Adamstown, Co. Dublin"), ("Riverstown", "Riverstown, Co. Cork"),
        ("Navan, Meath", "Navan, Co. Meath"), ("Navan Co Meath", "Navan, Co. Meath"),
        ("Donegal town", "Donegal, Co. Donegal"), ("Cahersiveen", "Co. Kerry"), ("Milltown Malbay", "Miltown Malbay"),
        ("Dublin city", "Dublin 1"), ("Dublin city centre", "Dublin 1"), ("Cork city centre", "Cork city"),
    ):
        found = page.evaluate("(q) => window.whichED.findPlaces(q).map(p => p.label)", typed)
        need(any(wanted in f for f in found[:5]), f"{typed}: {found[:5]}")


@case
def unlisted_places_never_list_a_far_town(new):
    """Real places missing from our list must not get a near-spelling elsewhere (round-3 N1)."""
    page = new().page
    for typed, own in (("Rathtoe", "Rathtoe, Co. Carlow"), ("Bellavary", "Bellavary, Co. Mayo"), ("Navan", "Navan, Co. Meath")):
        found = page.evaluate("(q) => window.whichED.findPlaces(q).map(p => p.label)", typed)
        need(found and found[0] == own, f"control: {typed} doesn't list itself first: {found[:3]}")
    for typed, county in (("Rathoe", "Carlow"), ("Kinsealey", "Dublin"), ("Ballyvary", "Mayo"), ("Brandon", "Kerry"),
                          ("Ballyconneely", "Galway"), ("Cloghane", "Kerry"), ("West Village", "Cork"), ("Nad", "Cork")):
        found = page.evaluate("(q) => window.whichED.findPlaces(q).map(p => p.label)", typed)
        need(not found or found[0].endswith(f"Co. {county}"), f"{typed} (Co. {county}) lists {found[:3]}")


@case
def typing_first_then_age_keeps_the_list(new):
    s = new()
    s.page.click("#where")
    s.page.keyboard.type("Navan")
    need(s.page.locator("#suggest").is_visible(), "control: no list after typing")
    s.page.locator("#care button[data-care=child]").click()
    need(s.focused() == "where", f"focus on {s.focused()}, not the town box")
    need(s.page.locator("#suggest button", has_text="Navan, Co. Meath").is_visible(), "the list hid after the age tap")
    s.page.keyboard.press("Escape")
    need(not s.page.locator("#suggest").is_visible(), "control: Escape didn't close the list")
    s.page.click("#where")
    need(s.page.locator("#suggest").is_visible(), "tapping the box didn't bring the list back")


@case
def a_tap_elsewhere_closes_the_list(new):
    s = new()
    s.page.click("#where")
    s.page.keyboard.type("Navan")
    need(s.page.locator("#suggest").is_visible(), "control: no list after typing")
    s.page.evaluate("document.querySelector('h1').click()")  # like iOS: the box keeps focus
    need(s.focused() == "where", "control: the box lost focus, so this didn't test iOS")
    need(not s.page.locator("#suggest").is_visible(), "a tap elsewhere left the list open")


@case
def data_with_no_safe_answer_shows_the_error(new):
    locations = real_locations()
    for site in locations.values():
        if isinstance(site, dict) and site.get("type") == "ed":
            site["min_age"], site["max_age"] = 16, None  # valid ages, but no ED for a child
    s = new(files={**report(), "locations.json": locations})
    s.care("child")
    s.place("Navan", "Navan, Co. Meath")
    need(s.page.locator("#fatal").is_visible(), "no error message when there's no safe answer")
    need(not s.page.locator("#answer").is_visible(), "an answer was shown anyway")


@case
def injury_unit_without_hours_still_answers(new):
    locations = real_locations()
    del locations["iu-mallow"]["hours"]
    s = new(files={**report(), "locations.json": locations})
    s.go("Mallow", "adult", "Mallow, Co. Cork")
    row = s.page.locator("#options li", has_text="Mallow Injury Unit").inner_text()
    need("Open now" not in row and "Check the hours" in row, row)


@case
def first_version_has_no_location_or_less_busy(new):
    s = new(files=report(counts={"chi-at-crumlin": (30, 10)}))
    need(s.page.locator("#locate").count() == 0 and "use my location" not in s.text().lower(), "Use my location is still offered")
    s.go("D12", "child", "Dublin 12")
    need("Much busier than usual" in s.page.inner_text("#answer"), "Crumlin isn't busy in this test")
    need("less busy" not in s.text().lower() and s.page.locator("#hint").count() == 0, "a less busy option is shown")
    need(s.page.evaluate("window.__geoCalls") == 0, "the page asked where the person is")


@case
def dont_put_off_care_sits_beside_the_answer(new):
    s = new(files=report(counts={"chi-at-crumlin": (30, 10)}))
    s.go("D12", "child", "Dublin 12")
    answer = s.page.inner_text("#answer")
    need("Much busier than usual" in answer, "Crumlin isn't busy in this test")
    need("Don't put off care" in answer, "nothing beside 'Much busier' says to go anyway")


@case
def border_answers_say_northern_ireland_is_left_out(new):
    for typed, option in (("Omeath", "Omeath, Co. Louth"), ("Muff", "Muff, Co. Donegal"),
                          ("Blacklion", "Blacklion, Co. Cavan"), ("Pettigo", "Pettigo, Co. Donegal")):
        s = new()
        s.go(typed, "adult", option)
        need("Northern Ireland" in s.page.inner_text("#answer"), f"{typed}: no Northern Ireland note")
    s = new()
    s.go("Navan", "adult", "Navan, Co. Meath")
    need("Northern Ireland" not in s.page.inner_text("#answer"), "Northern Ireland note far from the border")


@case
def unfair_or_old_reports_are_never_compared(new):
    busy = {"chi-at-crumlin": (30, 10)}
    control = new(files=report(counts=busy))
    control.go("D12", "child", "Dublin 12")
    need("Much busier than usual" in control.page.inner_text("#answer"), "control: never compares")
    for day, time, said in (("2026-11-23", "14:00", "2pm"), ("2026-11-22", "08:00", "Sunday"), ("2026-11-20", "08:00", "Friday")):
        s = new(files=report(day, time, busy))
        s.go("D12", "child", "Dublin 12")
        answer = s.page.inner_text("#answer")
        need("than usual" not in answer and "than usual" not in s.page.inner_text("#more"), f"compared a {day} {time} report")
        need(said in answer, f"{day} {time}: doesn't say when: {answer}")


@case
def afternoon_report_says_2pm_and_does_not_compare(new):
    s = new(files=report(time="14:00", counts={"our-lady-of-lourdes-hospital": (0, 10)}))
    s.go("Navan", "child", "Navan, Co. Meath")
    text = s.page.inner_text("main")
    answer = s.page.inner_text("#answer")
    need("2pm" in text, "no 2pm")
    need("than usual" not in answer, "compared a 2pm report")
    need("At 8am" not in answer, "says 8am")
    need("usual figures are from 8am" not in answer, "claims every usual day is an 8am report")


@case
def old_report_is_flagged(new):
    s = new(files=report("2026-11-20"))
    s.go("Navan", "child", "Navan, Co. Meath")
    stamp = s.page.locator("#stamp")
    need("late" in stamp.get_attribute("class") and "20 November" in stamp.inner_text(), stamp.inner_text())
    need("than usual" not in s.page.inner_text("#answer"), "compared a three-day-old report")


@case
def few_waiting_never_reads_as_much_busier(new):
    s = new(files=report(counts={"connolly-hospital": (4, 2)}))
    s.go("D15", "adult", "Dublin 15")
    need(s.answer() == "Connolly Hospital Blanchardstown", s.answer())
    need("Few waiting for a bed" in s.page.inner_text("#answer"), "4 against 2 isn't 'few'")


@case
def small_differences_never_read_as_busier(new):
    s = new(files=report(counts={"connolly-hospital": (6, 2.4)}))
    s.go("D15", "adult", "Dublin 15")
    need(s.answer() == "Connolly Hospital Blanchardstown", s.answer())
    answer = s.page.inner_text("#answer")
    need("6 people were waiting" in answer, f"the figure isn't shown: {answer}")
    need("busier" not in answer.lower(), "4 more people than usual reads as busier")


@case
def missing_figures_still_give_an_answer(new):
    today = {"report_date": "2026-11-23", "snapshot_time": "08:00"}
    for files in ({"latest.json": None, "history.json": None},
                  {"latest.json": {"nonsense": True}, "history.json": None},
                  {"latest.json": {**today, "hospitals": []}, "history.json": None},
                  {"latest.json": {**today, "hospitals": [None]}, "history.json": None},
                  {**report(), "history.json": {"national": [], "hospitals": {"chi-at-crumlin": 5}}}):
        s = new(files=files)
        need(s.go("Navan", "child", "Navan, Co. Meath") == DROGHEDA, s.answer())
        need(s.page.locator("#answer a.directions").is_visible(), "no Directions without figures")
        need("than usual" not in s.page.inner_text("#answer"), "compared without figures")
        need("Getting the latest" not in s.page.inner_text("#stamp"), f"stuck loading with {str(files)[:80]}")


@case
def missing_hospital_list_fails_safe(new):
    no_age = real_locations()
    del no_age["uh-kerry"]["min_age"]
    for locations in (None, no_age):
        s = new(files={**report(), "locations.json": locations})
        need(s.page.locator("#fatal").is_visible(), "no error shown")
        need("112" in s.text() and "999" in s.text(), "no emergency numbers")
        need(not s.page.locator("#answer").is_visible(), "answer shown without data")


@case
def page_never_dies_silently(new):
    s = new(site={"icons.js": None})
    need(s.go("Navan", "child", "Navan, Co. Meath") == DROGHEDA, "no answer without icons")
    s = new(site={"app.js": "this is not ( javascript"}, errors_expected=True)
    need(s.page.locator("#answer").count() == 1, "page didn't open")
    s.page.locator("#fatal").wait_for(state="visible", timeout=12000)
    need("112" in s.page.inner_text("#fatal"), "no emergency numbers when the script fails")
    s = new(js=False)
    text = s.text()
    need("needs JavaScript" in text and "112" in text, "nothing said with JavaScript off")


@case
def taps_while_loading_are_kept(new):
    s = new(files={**report(), "locations.json": "hold"})
    need(s.held, "the hospital list wasn't held")
    child = s.page.locator("#care button[data-care=child]")
    child.click()
    need(child.get_attribute("aria-checked") == "true", "a tap on Child while loading was lost")
    s.page.keyboard.type("Navan")
    s.release()
    s.page.locator("#suggest button", has_text="Navan, Co. Meath").first.click()
    need(s.answer() == DROGHEDA, s.answer())


@case
def injury_unit_hours_follow_irish_time(new):
    for now, words, zone in ((MONDAY_10AM, "Open now until 8pm", "Europe/Dublin"),
                             (datetime(2026, 11, 23, 22, 0, tzinfo=DUBLIN), "Closed now", "America/New_York"),
                             (datetime(2026, 7, 6, 7, 30, tzinfo=DUBLIN), "Opens at 8am", "Europe/Dublin")):
        s = new(now=now, zone=zone)
        s.go("Mallow", "adult", "Mallow, Co. Cork")
        row = s.page.locator("#options li", has_text="Mallow Injury Unit").inner_text()
        need(words in row, f"{now}: wanted {words}, got {row}")
    s = new(now=datetime(2026, 11, 25, 12, 0, tzinfo=DUBLIN))
    s.go("Ballina", "adult", "Ballina, Co. Mayo")
    need("Closed today" in s.page.locator("#options li", has_text="Ballina Injury Unit").inner_text(), "Ballina open on a Wednesday")


@case
def injury_units_never_say_open_on_public_holidays(new):
    def row(when, typed, option, unit):
        s = new(now=datetime(*when, 12, 0, tzinfo=DUBLIN))
        s.go(typed, "adult", option)
        found = s.page.locator("#options li", has_text=unit)
        need(found.count() == 1, f"{unit} not listed from {typed}")
        return found.inner_text()

    control = row((2026, 11, 23), "Monaghan", "Monaghan, Co. Monaghan", "Monaghan Injury Unit")
    need("Open now until 8pm" in control, f"control: {control}")
    for when, typed, option, unit in (
        ((2026, 12, 25), "Monaghan", "Monaghan, Co. Monaghan", "Monaghan Injury Unit"),   # HSE: closed
        ((2026, 12, 26), "Monaghan", "Monaghan, Co. Monaghan", "Monaghan Injury Unit"),   # HSE: closed
        ((2026, 12, 25), "Dundalk", "Dundalk, Co. Louth", "Dundalk Injury Unit"),          # HSE: closed
        ((2026, 12, 25), "D07", "Dublin 7", "Mater Smithfield Rapid Injury Clinic"),       # HSE: closed
        ((2026, 6, 1), "D07", "Dublin 7", "Mater Smithfield Rapid Injury Clinic"),         # June bank holiday
        ((2027, 3, 29), "Cashel", "Cashel, Co. Tipperary", "Cashel Injury Unit"),          # Easter Monday 2027
        ((2027, 12, 27), "Monaghan", "Monaghan, Co. Monaghan", "Monaghan Injury Unit"),   # Christmas 2027 is a Saturday
        ((2030, 2, 1), "Monaghan", "Monaghan, Co. Monaghan", "Monaghan Injury Unit"),     # St Brigid's: 1 Feb is a Friday
    ):
        text = row(when, typed, option, unit)
        need("Open now" not in text and "Holiday hours" in text, f"{when} {unit}: {text}")
    ordinary = row((2030, 2, 4), "Monaghan", "Monaghan, Co. Monaghan", "Monaghan Injury Unit")  # so not the Monday after
    need("Open now until 8pm" in ordinary, f"4 Feb 2030 is an ordinary Monday: {ordinary}")


@case
def open_tab_stays_current(new):
    s = new()
    s.go("Navan", "child", "Navan, Co. Meath")
    need("23 November" in s.page.inner_text("#stamp"), "control: wrong first report")
    before = s.requests("data/latest.json")
    s.files["latest.json"] = report("2026-11-24")["latest.json"]
    s.page.clock.set_fixed_time(datetime(2026, 11, 24, 11, 0, tzinfo=DUBLIN))
    s.come_back()
    s.page.locator("#stamp", has_text="24 November").wait_for()
    need(s.requests("data/latest.json") > before, "the figures weren't fetched again")
    s = new(now=datetime(2026, 11, 23, 19, 50, tzinfo=DUBLIN), clock="install")
    s.go("Mallow", "adult", "Mallow, Co. Cork")
    mallow = s.page.locator("#options li", has_text="Mallow Injury Unit")
    need("Open now until 8pm" in mallow.inner_text(), f"control: {mallow.inner_text()}")
    s.page.clock.fast_forward("01:11:00")
    s.page.locator("#options li", has_text="Mallow Injury Unit").filter(has_text="Closed now").wait_for()
    # a failed refetch keeps the report it already has
    s = new(files=report(counts={"chi-at-crumlin": (30, 10)}))
    s.go("D12", "child", "Dublin 12")
    need("Much busier than usual" in s.page.inner_text("#answer"), "control: Crumlin isn't busy")
    before = s.requests("data/latest.json")
    s.files["latest.json"] = None
    s.page.clock.set_fixed_time(datetime(2026, 11, 23, 10, 30, tzinfo=DUBLIN))
    s.come_back()
    for _ in range(40):
        if s.requests("data/latest.json") > before:
            break
        s.page.wait_for_timeout(100)
    need(s.requests("data/latest.json") > before, "no refetch after 30 minutes away")
    s.page.wait_for_timeout(500)
    need("Much busier than usual" in s.page.inner_text("#answer"), "a failed refetch threw away today's figures")
    need("today" in s.page.inner_text("#stamp"), s.page.inner_text("#stamp"))


@case
def focus_stays_after_returning_from_a_link(new):
    s = new()
    s.go("Mallow", "adult", "Mallow, Co. Cork")
    link = s.page.locator("#options a.details-link").last
    label = link.get_attribute("aria-label")
    link.focus()
    need(s.focused() == label, f"the link didn't take focus: {s.focused()}")
    s.come_back()
    need(s.focused() == label, f"focus moved to {s.focused()} after coming back")
    s.page.clock.set_fixed_time(datetime(2026, 11, 23, 21, 0, tzinfo=DUBLIN))
    s.come_back()
    s.page.locator("#options li", has_text="Mallow Injury Unit").filter(has_text="Closed now").wait_for()
    need(s.focused() == label, f"focus moved to {s.focused()} when the hours changed")


@case
def keyboard_alone_works(new):
    s = new()
    s.page.focus("#care button[data-care=adult]")
    s.page.keyboard.press("ArrowRight")
    need(s.page.locator("#care button[data-care=child]").get_attribute("aria-checked") == "true", "arrow key didn't choose Child")
    s.page.keyboard.press("Tab")
    need(s.focused() == "where", "Tab didn't reach the town box")
    s.page.keyboard.type("Ballina")
    need(s.page.locator("#suggest").is_visible(), "no list")
    s.page.keyboard.press("ArrowDown")
    s.page.keyboard.press("Enter")
    need(s.page.locator("#answer").is_visible(), "Enter on a listed town didn't choose it")


@case
def directions_are_on_screen_after_choosing(new):
    for height in (640, 780):
        s = new(files=report(counts={"chi-at-crumlin": (30, 10)}), height=height)
        s.go("D12", "child", "Dublin 12")
        box = s.page.locator("#answer a.directions").bounding_box()
        need(box["y"] >= 0 and box["y"] + box["height"] <= height, f"Directions off screen at {height}px: {box}")


@case
def typed_html_is_not_run(new):
    s = new()
    s.page.fill("#where", "<img src=x onerror=window.pwned=1>")
    need(s.page.input_value("#where").startswith("<img"), "not typed")
    need(s.page.evaluate("!window.pwned && !document.querySelector('main img')"), "typed HTML was inserted")


@case
def says_it_is_a_test_and_not_the_hse(new):
    s = new()
    text = s.text()
    for words in ("Test version", "Not the HSE", "not a waiting time", "112", "999"):
        need(words.lower() in text.lower(), f"missing: {words}")
    need("noindex" in (s.page.get_attribute("meta[name=robots]", "content") or ""), "search engines allowed")


@case
def readable_on_every_screen(new):
    busy = report(counts={"chi-at-crumlin": (30, 10)})
    for scheme in ("light", "dark"):
        for name, width, height in (("small", 320, 700), ("phone", 360, 780), ("desktop", 1280, 900)):
            s = new(files=busy, width=width, height=height, scheme=scheme)
            for state in ("empty", "list", "answer"):
                if state == "list":
                    s.care("child")
                    s.page.click("#where")
                    s.page.keyboard.type("D12")
                    need(s.page.locator("#suggest").is_visible(), "list didn't open")
                if state == "answer":
                    s.page.locator("#suggest button", has_text="Dublin 12").first.click()
                    need(s.page.locator("#answer").is_visible() and "Much busier" in s.page.inner_text("#answer"), "busy answer didn't open")
                s.page.evaluate("document.fonts.ready")
                s.page.wait_for_timeout(200)  # let colour transitions finish
                m = s.page.evaluate(MEASURE)
                where = f"{name} {scheme} {state}"
                need(m["texts"] > 10, f"{where}: nothing was measured")
                need(m["overflow"] <= 0, f"{where}: scrolls sideways")
                for kind in ("small", "contrast", "targets"):
                    need(not m[kind], f"{where}: {kind} {m[kind][:4]}")
                need(m["noWord"] == 0, f"{where}: status without a word")
                s.page.screenshot(path=str(OUT / f"{name}-{scheme}-{state}.png"), full_page=True)
            need(s.page.evaluate("document.fonts.check('700 17px \"Atkinson Hyperlegible Next\"')"), "font missing")
            need(not s.outside, f"outside requests: {s.outside}")


@case
def real_data_renders(new):
    now = datetime.now(DUBLIN)
    s = new(files={}, now=now)
    need(s.go("Navan", "child", "Navan, Co. Meath") == DROGHEDA, s.answer())
    day = date.fromisoformat(json.loads((ROOT / "data/latest.json").read_text(encoding="utf-8"))["report_date"])
    stamp = s.page.inner_text("#stamp")
    wanted = "today" if day == now.date() else f"{day.day} {day.strftime('%B')}"
    need(wanted in stamp, f"wanted {wanted} in: {stamp}")
    s.page.screenshot(path=str(OUT / "real-data-phone.png"), full_page=True)


@case
def typing_or_enter_never_chooses_a_place(new):
    for typed in ("Navan", "Kildare", "Tipperary", "Cashel", "Adamstown", "Riverstown", "St Johns",
                  "Killea", "Ballyduff", "Dublin", "Newport", "Kells", "D1"):
        s = new()
        s.care("child")
        s.page.click("#where")
        s.page.keyboard.type(typed)
        need(s.page.locator("#suggest button").count() >= 1, f"{typed}: nothing listed, so nothing was tested")
        need(not s.page.locator("#answer").is_visible(), f"typing {typed} chose {s.page.input_value('#where')}")
        s.page.keyboard.press("Enter")
        need(not s.page.locator("#answer").is_visible(), f"{typed} + Enter chose {s.page.input_value('#where')}")
        need(s.page.locator("#suggest").is_visible(), f"{typed}: list closed")
        need(s.focused() == "where", f"{typed}: Enter moved focus to {s.focused()}, so a second Enter would choose")


@case
def dublin_lists_its_districts_first(new):
    first = new().page.evaluate("() => window.whichED.findPlaces('Dublin').slice(0, 3).map(p => p.name)")
    need(first == ["Dublin 1", "Dublin 2", "Dublin 3"], str(first))


@case
def short_dublin_codes_work(new):
    find = "(q) => window.whichED.findPlaces(q).map(p => p.key)"
    page = new().page
    for typed, first in (("D1", "D01"), ("D2", "D02"), ("D4", "D04"), ("D6", "D06"), ("D8", "D08")):
        keys = page.evaluate(find, typed)
        need(keys and keys[0] == first, f"{typed}: {keys[:3]}")
    need("D6W" in page.evaluate(find, "D6"), "D6 doesn't offer D6W")


@case
def same_name_villages_need_a_tap(new):
    for typed in ("Monkstown", "Raheen", "Douglas"):
        s = new()
        s.care("adult")
        s.page.click("#where")
        s.page.keyboard.type(typed)
        need(s.page.input_value("#where") == typed, f"{typed} not typed")
        need(not s.page.locator("#answer").is_visible(), f"{typed} picked itself")
        need(s.page.locator("#suggest button", has_text="Co.").count() > 0, f"{typed}: no county to choose")


@case
def water_crossings_get_the_hospital_by_road(new):
    for town, care, expected in (
        ("Kilrush", "adult", "University Hospital Limerick"), ("Kilrush", "child", "University Hospital Limerick"),
        ("Kilkee", "child", "University Hospital Limerick"), ("Doonbeg", "adult", "University Hospital Limerick"),
        ("Killybegs", "child", "Letterkenny University Hospital"), ("Duncannon", "adult", "Wexford General Hospital"),
        ("Tallow", "adult", "Mercy University Hospital"), ("Tallow", "child", "Cork University Hospital"),
    ):
        s = new()
        got = s.go(town, care)
        need(got == expected, f"{town} {care}: {got}")
        need("quicker by road" in s.page.inner_text("#answer"), f"{town}: reason not shown")
    s = new()
    s.go("Kilrush", "adult")
    kerry = s.page.locator("#options li", has_text="University Hospital Kerry")
    need(kerry.count() == 1 and "slower by road" in kerry.inner_text(), "Kerry isn't marked slower by road")
    s = new()
    need(s.go("Cloughjordan", "adult") == "Portiuncula University Hospital", "road fix used where it isn't robust")


@case
def injury_unit_advice_is_for_minor_injuries(new):
    text = new().page.inner_text(".unsure").lower()
    for words in ("minor injury", "suspected simple fracture", "serious"):
        need(words in text, f"missing: {words}")


@case
def works_while_figures_are_still_loading(new):
    s = new(files={**report(), "latest.json": "hold"})
    need(s.held, "the request wasn't held")
    need(s.go("Navan", "child", "Navan, Co. Meath") == DROGHEDA, "no answer while loading")


@case
def choice_shows_in_forced_colours(new):
    s = new()
    s.page.emulate_media(forced_colors="active")
    need(s.page.evaluate("matchMedia('(forced-colors: active)').matches"), "forced colours didn't switch on")
    s.care("child")
    s.page.evaluate("document.activeElement.blur()")  # so a focus ring can't pass for a selection
    s.page.mouse.move(1, 1)
    s.page.wait_for_timeout(250)
    look = s.page.evaluate("""() => {
      const probe = document.createElement('i');
      probe.style.backgroundColor = 'Highlight';
      document.body.append(probe);
      const highlight = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return { highlight, chosen: [...document.querySelectorAll('#care button')].map(b => getComputedStyle(b).backgroundColor) }; }""")
    need(look["chosen"][1] == look["highlight"], f"Child isn't in the system highlight colour: {look}")
    need(look["chosen"][0] != look["highlight"], f"Adult looks chosen too: {look}")


@case
def choosing_age_last_still_shows_directions(new):
    s = new(height=700)
    s.place("Navan", "Navan, Co. Meath")
    s.care("child")
    need(s.page.locator("#answer").is_visible(), "no answer")
    box = s.page.locator("#answer a.directions").bounding_box()
    need(box["y"] >= 0 and box["y"] + box["height"] <= 700, f"Directions off screen: {box}")


def main():
    unknown = ONLY - {fn.__name__ for fn in CASES}
    need(not unknown, f"no such checks: {sorted(unknown)}")
    failed = ran = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", chromium_sandbox=True)
        for fn in CASES:
            if ONLY and fn.__name__ not in ONLY:
                continue
            ran += 1
            opened = []

            def new(**options):
                opened.append(Site(browser, **options))
                return opened[-1]

            try:
                fn(new)
                errors = [e for s in opened if not s.errors_expected for e in s.errors]
                need(not errors, f"page errors: {errors[:3]}")
                print(f"PASS {fn.__name__}")
            except Exception as e:  # report every case rather than stopping at the first
                failed += 1
                lines = str(e).splitlines() or [traceback.format_exc().splitlines()[-1]]
                print(f"FAIL {fn.__name__}: {lines[0][:260]}")
            finally:
                for s in opened:
                    s.close()
        browser.close()
    print(f"{ran - failed} of {ran} passed")
    sys.exit(1 if failed or not ran else 0)


if __name__ == "__main__":
    main()
