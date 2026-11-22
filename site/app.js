/* whichED: one clear answer.
   Reads data/locations.json (who treats whom, and where), places.json (towns and Eircode areas),
   data/latest.json and data/history.json (the HSE's daily figures).
   Nothing the visitor types leaves the device or is saved. */
(() => {
  "use strict";

  const ADULT_AGE = 16;
  const FEW = 5;               // under this today and usually: "few". A change smaller than this is "about usual".
  const IU_MAX_KM = 80;
  const FIGURES_WAIT_MS = 10000;
  const ESSENTIALS_WAIT_MS = 20000;
  const REFETCH_MS = 10 * 60 * 1000;  // a tab that comes back after this long gets the figures again
  const BORDER_COUNTIES = ["Donegal", "Leitrim", "Cavan", "Monaghan", "Louth"];
  const STATUS = {
    much: { label: "Much busier than usual", bars: 4 },
    busier: { label: "Busier than usual", bars: 3 },
    usual: { label: "About usual", bars: 2 },
    quieter: { label: "Quieter than usual", bars: 1 },
    few: { label: "Few waiting for a bed", bars: 0 },
    none: { label: "No comparison today", bars: 0 },
  };
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  const $ = (selector) => document.querySelector(selector);
  const input = $("#where");
  const suggest = $("#suggest");
  const careButtons = [...document.querySelectorAll("#care button")];

  let sites = [];
  let places = [];
  let counties = new Set();
  let loadingFigures = true;
  let figures = new Map();
  let alwaysZero = new Set();
  let report = null;
  let fetchedAt = 0;
  let shownAt = "";            // the day and opening hours the page last showed
  let place = null;
  let care = null;

  // ---------- small helpers ----------
  function el(tag, className, ...children) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.append(...children.filter((c) => c !== null && c !== undefined));
    return node;
  }

  function icon(name) {
    const path = window.ICONS && window.ICONS[name];
    if (!path) return document.createTextNode("");  // icons.js didn't load: carry on without pictures
    const t = document.createElement("template");
    // Only ever our own constants from icons.js, never data or typed text.
    t.innerHTML = `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
    return t.content.firstChild;
  }

  async function getJSON(path, waitMs) {
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), waitMs);
    try {
      const r = await fetch(path, { cache: "no-cache", signal: stop.signal });
      if (!r.ok) throw new Error(`${path}: ${r.status}`);
      return await r.json();
    } finally {
      clearTimeout(timer);
    }
  }

  const simple = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/['’]/g, "").replace(/[^a-z0-9]+/g, " ").trim();

  function distanceKm(a, b) {
    const rad = Math.PI / 180;
    const dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(x));
  }
  const formatKm = (km) => (km < 1 ? "Under 1 km" : km < 10 ? `${km.toFixed(1)} km` : `${Math.round(km)} km`);

  // ---------- time, always Irish ----------
  function irishNow() {
    const parts = {};
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Dublin", year: "numeric", month: "2-digit", day: "2-digit",
      weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date()).forEach((p) => { parts[p.type] = p.value; });
    return { date: `${parts.year}-${parts.month}-${parts.day}`, day: DAYS.indexOf(parts.weekday), time: `${parts.hour}:${parts.minute}` };
  }

  function clockText(hhmm) {
    const [h, m] = hhmm.split(":").map(Number);
    return `${h % 12 || 12}${m ? `.${String(m).padStart(2, "0")}` : ""}${h < 12 ? "am" : "pm"}`;
  }

  const dateText = (iso, options) =>
    new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", ...options }).replace(",", "");

  const reportAge = () => (report ? Math.round((Date.parse(irishNow().date) - Date.parse(report.report_date)) / 864e5) : null);

  function reportWhen() {
    const time = clockText(report.snapshot_time);
    return reportAge() === 0 ? `${time} today` : `${time} on ${dateText(report.report_date, { weekday: "long" })}`;
  }

  // Days an Injury Unit may keep holiday hours: the ten public holidays listed on HSE pages,
  // plus the weekday after one that falls at a weekend. Worked out by rule, because the HSE's
  // own list puts the summer ones a day early.
  const holidayYears = new Map();
  function holidayHours(iso) {
    const year = Number(iso.slice(0, 4));
    if (!holidayYears.has(year)) {
      const day = (m, d) => new Date(Date.UTC(year, m - 1, d));
      const firstMonday = (m) => day(m, 1 + ((8 - day(m, 1).getUTCDay()) % 7));
      const a = year % 19, b = Math.floor(year / 100), c = year % 100, d = Math.floor(b / 4), e = b % 4;
      const f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30;
      const l = (32 + 2 * e + 2 * Math.floor(c / 4) - h - (c % 4)) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
      const easter = day(Math.floor((h + l - 7 * m + 114) / 31), ((h + l - 7 * m + 114) % 31) + 1);
      const fixed = [day(1, 1), day(3, 17), day(12, 25), day(12, 26)];
      const brigid = day(2, 1).getUTCDay() === 5 ? day(2, 1) : firstMonday(2);  // 1 February when that is a Friday
      const all = [...fixed, brigid, firstMonday(5), firstMonday(6), firstMonday(8),
        day(10, 31 - ((day(10, 31).getUTCDay() + 6) % 7)), day(easter.getUTCMonth() + 1, easter.getUTCDate() + 1)];
      const dates = new Set(all.map((x) => x.toISOString().slice(0, 10)));
      for (const x of fixed) {
        if (x.getUTCDay() % 6 !== 0) continue;
        const next = new Date(x);
        do next.setUTCDate(next.getUTCDate() + 1);
        while (next.getUTCDay() % 6 === 0 || dates.has(next.toISOString().slice(0, 10)));
        dates.add(next.toISOString().slice(0, 10));
      }
      holidayYears.set(year, dates);
    }
    return holidayYears.get(year).has(iso);
  }

  function hoursText(hours) {
    const { date, day, time } = irishNow();
    if (holidayHours(date)) return "Holiday hours may apply today. Check before you go.";
    if (!Array.isArray(hours)) return "Check the hours before you go.";
    const today = hours[day];
    if (!today) return "Closed today";
    if (time < today[0]) return `Closed now. Opens at ${clockText(today[0])}`;
    if (time < today[1]) return `Open now until ${clockText(today[1])}`;
    return "Closed now";
  }

  // What changes on screen as time passes, without anyone touching it.
  const timeShown = () => [irishNow().date, ...sites.filter((s) => s.type === "iu").map((s) => hoursText(s.hours))].join("|");

  // ---------- who can go where ----------
  // The main answer must treat every age in the group.
  function seesEveryone(site, careFor) {
    return careFor === "child" ? site.min_age === 0 : site.max_age === null && site.min_age <= ADULT_AGE;
  }
  // Other places may treat part of the group, and then say their age limit.
  function seesSome(site, careFor) {
    return careFor === "child" ? site.min_age < ADULT_AGE : seesEveryone(site, careFor);
  }

  // ---------- how busy ----------
  const canCompare = () => report !== null && report.snapshot_time === "08:00" && reportAge() === 0;

  function statusOf(site) {
    const f = figures.get(site.id);
    if (!f || !canCompare() || alwaysZero.has(site.id)) return "none";
    const today = f.total_trolleys, usual = f.usual_total_trolleys;
    if (typeof today !== "number" || typeof usual !== "number") return "none";
    if (today === 0 && usual === 0) return "none";  // always zero, even if history.json didn't load
    if (today < FEW && usual < FEW) return "few";
    if (Math.abs(today - usual) < FEW) return "usual";
    const ratio = today / Math.max(usual, 1);
    if (ratio >= 1.5) return "much";
    if (ratio >= 1.15) return "busier";
    if (ratio <= 0.6) return "quieter";
    return "usual";
  }

  function sentence(site) {
    if (loadingFigures) return "Getting the latest figures…";
    const f = figures.get(site.id);
    if (!f || typeof f.total_trolleys !== "number") return "We don't have a bed-wait figure for this hospital.";
    if (alwaysZero.has(site.id)) return "This hospital reports nobody waiting for a bed every day, so we can't compare it.";
    const n = f.total_trolleys;
    const who = n === 0 ? "nobody was" : n === 1 ? "1 person was" : `${n} people were`;
    const now = `At ${reportWhen()}, ${who} waiting for a bed here.`;
    if (report.snapshot_time !== "08:00") return `${now} We only compare 8am reports, so we can't compare this one.`;
    if (!canCompare()) return `${now} We only compare today's report.`;
    if (typeof f.usual_total_trolleys !== "number") return `${now} We don't have enough days to compare yet.`;
    const usual = Math.round(f.usual_total_trolleys);
    return `${now} Over the last ${f.baseline_days} days it was usually ${usual === 0 ? "nobody" : `about ${usual}`}.`;
  }

  function results(from, careFor) {
    const near = sites
      .map((s) => ({ ...s, km: distanceKm(from, s), status: s.type === "ed" ? statusOf(s) : null }))
      .sort((a, b) => a.km - b.km);
    const eds = near.filter((s) => s.type === "ed");
    const closest = eds.find((s) => seesEveryone(s, careFor));
    // Where water or hills make the closest on the map much slower to drive to (docs/site-data.md).
    const quicker = eds.find((s) => s.id === (from.road && from.road[careFor]) && seesEveryone(s, careFor));
    const main = quicker || closest;
    const byRoad = main.id !== closest.id;
    const others = [
      ...eds.filter((s) => s.id !== main.id && seesSome(s, careFor)).slice(0, 3),
      ...near.filter((s) => s.type === "iu" && s.km <= IU_MAX_KM && seesSome(s, careFor)).slice(0, 2),
    ].sort((a, b) => a.km - b.km)
      .map((s) => ({ ...s, slower: byRoad && s.type === "ed" && s.km < main.km }));
    return { main, others, byRoad };
  }

  // ---------- rendering ----------
  function statusNode(key) {
    let mark;
    if (key === "none") {
      mark = icon("info");
    } else {
      mark = el("span", "meter", ...[1, 2, 3, 4].map((i) => el("i", i <= STATUS[key].bars ? "on" : "")));
      mark.setAttribute("aria-hidden", "true");
    }
    return el("span", `status st-${key}`, mark, el("span", "", STATUS[key].label));
  }

  function outLink(className, href, label, ...children) {
    const a = el("a", className, ...children);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.setAttribute("aria-label", `${label} (opens in a new tab)`);
    return a;
  }

  // Name and Eircode, not a map point, so Maps finds the building itself.
  function directions(site, className = "directions", text = "Get directions") {
    const to = encodeURIComponent(`${site.name}, ${site.eircode}, Ireland`);
    return outLink(className, `https://www.google.com/maps/dir/?api=1&destination=${to}`, `${text} to ${site.name}`, icon("navigation"), text);
  }

  function renderAnswer(main, byRoad) {
    const name = el("h2", "", main.name);
    name.id = "answer-name";
    name.tabIndex = -1;
    const kind = care === "child" ? "Nearest emergency department for a child" : "Nearest emergency department";
    $("#answer").replaceChildren(...[
      el("span", "kicker", kind),
      name,
      el("span", "where", `${main.town} · ${formatKm(main.km)} from near ${place.label}`),
      byRoad ? el("span", "by-road", "Chosen because it's quicker by road from here.") : null,
      BORDER_COUNTIES.includes(place.county) ? el("span", "by-road", "Hospitals in Northern Ireland aren't included.") : null,
      statusNode(main.status),
      el("p", "", sentence(main)),
      el("p", "go-anyway", "Don't put off care because of anything on this page."),
      directions(main, "btn directions"),
      outLink("hse-link", main.url, `Check ${main.name} on the HSE website`, "Check it on the HSE website", icon("arrow-up-right")),
    ].filter((node) => node !== null));
  }

  function optionRow(site) {
    const type = site.type === "iu" ? "Injury Unit" : site.max_age !== null ? "Children's emergency department" : "Emergency department";
    const limit = care === "child" && site.min_age > 0 ? el("span", "limit", `Age ${site.min_age} and over only`) : null;
    const state = site.type === "iu" ? el("span", "hours", icon("clock"), hoursText(site.hours))
      : site.status !== "none" ? statusNode(site.status) : null;
    return el("li", "option",
      el("span", "option-top", el("span", "name", site.name), el("span", "km", formatKm(site.km))),
      el("span", "meta", `${site.town} · ${type}`),
      limit,
      site.slower ? el("span", "limit", "Closer on the map, slower by road") : null,
      state,
      el("span", "option-links",
        directions(site, "directions", "Directions"),
        outLink("details-link", site.url, `${site.name} details on the HSE website`, "HSE details", icon("arrow-up-right"))));
  }

  function renderStamp() {
    const stamp = $("#stamp");
    let late = !loadingFigures;
    let text = loadingFigures ? "Getting the latest HSE figures…"
      : "Today's HSE figures didn't load. We can still show you the nearest place.";
    if (report) {
      const day = dateText(report.report_date, { weekday: "long", day: "numeric", month: "long" });
      const time = clockText(report.snapshot_time);
      late = reportAge() !== 0;
      text = late
        ? `These figures are from ${time} on ${day}. We don't have a newer report yet.`
        : `Figures from ${time} today, ${day}. From the HSE's daily report.`;
    }
    stamp.classList.toggle("late", late);
    stamp.replaceChildren(icon("clock"), el("span", "", text));
  }

  function announce(text) {
    const box = $("#announce");
    if (box.textContent !== text) box.textContent = text;
  }

  // Every redraw goes through here: if the data can't give a safe answer, say so rather than go blank.
  function render() {
    try {
      draw();
    } catch (err) {
      console.warn(err);
      fatal();
    }
  }

  function draw() {
    shownAt = timeShown();
    const ready = place !== null && care !== null && sites.length > 0;
    $("#answer").hidden = !ready;
    $("#more").hidden = !ready;
    $("#next").hidden = ready;
    renderStamp();
    if (!ready) {
      $("#next").textContent = care ? "Now type your town, or the first 3 characters of your Eircode."
        : place ? "Now choose Adult or Child." : "Choose Adult or Child, then your town.";
      $("#answer").replaceChildren();
      $("#options").replaceChildren();
      announce("");
      return;
    }
    // Redrawing must not drop keyboard focus, for example on coming back from a link.
    const was = document.activeElement;
    const inside = $("#answer").contains(was) || $("#more").contains(was);
    const label = inside && was.getAttribute("aria-label");
    const { main, others, byRoad } = results(place, care);
    renderAnswer(main, byRoad);
    $("#options").replaceChildren(...others.map(optionRow));
    if (inside) {
      const same = label && [...document.querySelectorAll("#answer a, #more a")].find((a) => a.getAttribute("aria-label") === label);
      (same || $("#answer-name")).focus({ preventScroll: true });
    }
    announce(`Nearest emergency department for ${care === "child" ? "a child" : "an adult"}: ${main.name}.`);
  }

  // ---------- town and Eircode box ----------
  function makePlace([name, county, key, lat, lon, road]) {
    const district = /^Dublin \d|^Cork city (north|south)$/.test(name);
    const names = name.replace(/\)$/, "").split(" (").map(simple);
    return {
      name, county, key, lat, lon, road, district, names,
      joined: names.map((n) => n.replace(/ /g, "")),
      countySearch: simple(county),
      label: district || !county ? name : `${name}, Co. ${county}`,
      labelSearch: simple(district || !county ? name : `${name}, Co. ${county}`),
    };
  }

  const isKey = (q) => /^[a-z]\d[0-9w]?$/.test(q);

  function search(q) {
    const joined = q.replace(/ /g, "");
    const scored = [];
    for (const p of places) {
      let score = -1;
      if (p.names.includes(q) || p.labelSearch === q || (q === "dublin" && p.district && p.county === "Dublin")) score = 0;
      else if (p.names.some((n) => n.startsWith(q))) score = 1;
      // Without spaces ("dunlaoghaire") only from 4 letters: "nad" mustn't find Na Dúnaibh in Donegal.
      else if (p.names.some((n) => n.includes(` ${q}`)) || (joined.length >= 4 && p.joined.some((n) => n.startsWith(joined)))) score = 2;
      else if (p.countySearch.startsWith(q.replace(/^co(unty)? /, ""))) score = 3;
      if (score >= 0) scored.push({ p, score });
    }
    return scored.sort((a, b) => a.score - b.score).slice(0, 30).map((x) => x.p);
  }

  function findPlaces(typed) {
    const q = simple(typed);
    if (!q) return [];
    if (isKey(q)) {
      // D1 means D01 (and D6 could be D6W), but may also be someone part-way through typing D15.
      const meant = /^d[1-9]$/.test(q) ? (q === "d6" ? ["d06", "d6w"] : [`d0${q[1]}`]) : [];
      const inKey = (test) => {
        const found = places.filter((p) => test(p.key.toLowerCase()));
        return [...found.filter((p) => p.district), ...found.filter((p) => !p.district)];
      };
      return [...meant.flatMap((m) => inKey((k) => k === m)), ...inKey((k) => k.startsWith(q) && !meant.includes(k))];
    }
    const found = search(q);
    if (found.length) return found;
    // "Navan, Meath" or "Navan Co Meath"
    const withCounty = q.match(/^(.+?) (?:co |county )?([a-z]+)$/);
    if (withCounty && counties.has(withCounty[2])) {
      const inCounty = search(withCounty[1]).filter((p) => p.countySearch === withCounty[2]);
      if (inCounty.length) return inCounty;
    }
    // "Donegal town", "Dublin city centre": only a place with exactly that name. Never a near spelling,
    // which can be a different place far away (a real place missing from our list must say "No match").
    const bare = q.replace(/ (town|village|city|city centre|city center|centre|center)$/, "");
    if (bare === q || !bare) return [];
    return search(bare).filter((p) => p.names.includes(bare) || p.names.includes(`${bare} city`) || (bare === "dublin" && p.district));
  }

  function showList() {
    if (!input.value.trim()) {
      suggest.hidden = true;
      return;
    }
    if (!places.length) return showRows([], "Getting the town list…");
    const found = findPlaces(input.value);
    showRows(found, !found.length ? "No match. Try a bigger town nearby, or the first 3 characters of your Eircode."
      : isKey(simple(input.value)) ? "Not here? Type your town's name instead."
        : "Not here? Try a bigger town nearby, or the first 3 characters of your Eircode.");
  }

  function showRows(found, tip) {
    const rows = found.map((p) => {
      const b = el("button", "", el("span", "", p.label), el("span", "key", p.key));
      b.type = "button";
      b.addEventListener("click", () => choose(p));
      return el("li", "", b);
    });
    suggest.replaceChildren(...rows, el("li", "no-match", tip));
    suggest.scrollTop = 0;
    suggest.hidden = false;
  }

  // The only way to choose a place: a tap (or Enter) on a listed town, which shows its county.
  function choose(p) {
    place = p;
    input.value = p.label;
    suggest.hidden = true;
    $("#locmsg").textContent = "";
    render();
    if (!$("#fatal").hidden) return;
    if (care) {
      $("#answer-name").focus({ preventScroll: true });
      showAnswer();
    } else {
      careButtons[0].focus();
    }
  }

  function showAnswer() {
    $("#answer").scrollIntoView({ block: "start" });
  }

  function onType() {
    $("#locmsg").textContent = "";
    const full = input.value.match(/^\s*([a-z]\d[\dw])\s*[a-z\d]{1,4}\s*$/i);
    if (full) {
      input.value = full[1].toUpperCase();
      $("#locmsg").textContent = "We only use the first 3 characters. Pick the town nearest you.";
    }
    place = null;
    showList();
    render();
  }

  function onInputKey(e) {
    if (e.key === "Escape") suggest.hidden = true;
    if (e.key === "Enter") {
      // The phone's Done key. It never chooses a town by itself: same names repeat around the country.
      e.preventDefault();
      if (place) return care ? showAnswer() : careButtons[0].focus();
      if (!input.value.trim()) return;
      showList();
      if (places.length) $("#locmsg").textContent = "Tap your town in the list.";
    }
    if (e.key === "ArrowDown" && !suggest.hidden) {
      e.preventDefault();
      const first = suggest.querySelector("button");
      if (first) first.focus();
    }
  }

  function onListKey(e) {
    const buttons = [...suggest.querySelectorAll("button")];
    const i = buttons.indexOf(document.activeElement);
    if (e.key === "Escape") {
      input.focus();
      suggest.hidden = true;
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      (buttons[i + (e.key === "ArrowDown" ? 1 : -1)] || input).focus();
    }
  }

  // ---------- Adult / Child ----------
  function setCare(button) {
    care = button.dataset.care;
    careButtons.forEach((b) => {
      b.setAttribute("aria-checked", String(b === button));
      b.tabIndex = b === button ? 0 : -1;
    });
    render();
  }

  function onCareKey(e, i) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const next = careButtons[(i + step + careButtons.length) % careButtons.length];
    next.focus();
    setCare(next);
  }

  // ---------- start ----------
  function fatal() {
    $("#fatal").hidden = false;
    for (const id of ["#care", "#care-label", "#care-help", "label.q", "#place", "#locmsg", "#next", "#answer", "#more"]) $(id).hidden = true;
  }

  // The figures in latest.json and history.json, or null if latest.json isn't usable.
  function readFigures(latest, history) {
    const valid = latest && Array.isArray(latest.hospitals) && latest.hospitals.every((h) => h && typeof h.id === "string")
      && /^\d{4}-\d\d-\d\d$/.test(latest.report_date) && /^\d\d:\d\d$/.test(latest.snapshot_time);
    if (!valid) return null;
    const read = { report: latest, figures: new Map(latest.hospitals.map((h) => [h.id, h])), alwaysZero: new Set() };
    for (const [id, series] of Object.entries((history && history.hospitals) || {})) {
      if (!Array.isArray(series)) continue;
      const counts = series.map((row) => (Array.isArray(row) ? row[1] : null)).filter((v) => typeof v === "number");
      const today = read.figures.has(id) && read.figures.get(id).total_trolleys;
      if (counts.length >= 7 && counts.every((v) => v === 0) && !today) read.alwaysZero.add(id);
    }
    return read;
  }

  function useFigures(read) {
    report = read ? read.report : null;
    figures = read ? read.figures : new Map();
    alwaysZero = read ? read.alwaysZero : new Set();
  }

  async function loadFigures() {
    fetchedAt = Date.now();
    const [latest, history] = await Promise.all(
      ["data/latest.json", "data/history.json"].map((path) => getJSON(path, FIGURES_WAIT_MS).catch(() => null)));
    const first = loadingFigures;
    loadingFigures = false;
    const before = { report, figures, alwaysZero };
    let read = null;
    try {
      read = readFigures(latest, history);
    } catch (err) {
      console.warn(err);
    }
    if (!read && !first) return render();  // a failed refetch keeps the report we already have
    try {
      useFigures(read);
      draw();
    } catch (err) {  // a broken figures file must never stop the page: go back to what worked
      console.warn(err);
      useFigures(first ? null : before);
      render();
    }
  }

  // Opening hours, "today" and the figures change while a tab sits open.
  function stayCurrent() {
    if (document.hidden || !sites.length) return;
    if (timeShown() !== shownAt) render();
    if (!loadingFigures && Date.now() - fetchedAt > REFETCH_MS) loadFigures();
  }

  async function loadEssentials() {
    try {
      const [locations, placeRows] = await Promise.all(
        ["data/locations.json", "places.json"].map((path) => getJSON(path, ESSENTIALS_WAIT_MS)));
      const list = Object.entries(locations).filter(([id]) => !id.startsWith("_")).map(([id, s]) => ({ id, ...s }));
      // Without age rules there's no safe answer, so show the error rather than guess.
      if (!list.length || list.some((s) => !Number.isInteger(s.min_age) || s.max_age === undefined)) throw new Error("age rules missing");
      places = placeRows.map(makePlace);
      counties = new Set(places.map((p) => p.countySearch));
      sites = list;
      if (/^\d{4}-\d\d-\d\d$/.test(locations._checked)) {
        $("#checked").textContent = dateText(locations._checked, { day: "numeric", month: "long", year: "numeric" });
      }
    } catch (err) {
      console.warn(err);
      fatal();
      return;
    }
    // For checks/site_check.py.
    window.whichED = { get places() { return places; }, results, findPlaces };
    if (input.value && document.activeElement === input) showList();  // typed while the page was loading
    render();
    document.addEventListener("visibilitychange", stayCurrent);
    setInterval(stayCurrent, 60000);
    loadFigures();
  }

  function start() {
    try {
      // Shown by the page itself only if this script never runs (index.html, style.css).
      $("#fatal").hidden = true;
      $("#fatal").classList.remove("wait");
      document.querySelectorAll("[data-icon]").forEach((n) => n.replaceChildren(icon(n.dataset.icon)));
      // Listening before the data arrives, so early taps count.
      careButtons.forEach((b, i) => {
        b.addEventListener("click", () => {
          setCare(b);
          if (place) showAnswer();
          else input.focus();  // next step, so a parent is three taps from directions
        });
        b.addEventListener("keydown", (e) => onCareKey(e, i));
      });
      input.addEventListener("input", onType);
      input.addEventListener("keydown", onInputKey);
      input.addEventListener("focus", () => {
        if (place) input.select();
        else showList();
      });
      suggest.addEventListener("keydown", onListKey);
      input.addEventListener("click", () => {
        if (!place && suggest.hidden) showList();  // focus events don't fire again on a box that already has focus
      });
      document.addEventListener("click", (e) => {
        // An Adult/Child tap moves to the box and opens the list, so it mustn't close it here.
        if (!$("#place").contains(e.target) && !$("#care").contains(e.target)) suggest.hidden = true;
      });
      $("#place").addEventListener("focusout", (e) => {
        if (e.relatedTarget && !$("#place").contains(e.relatedTarget)) suggest.hidden = true;
      });
      render();
    } catch (err) {
      console.warn(err);
      fatal();
      return;
    }
    loadEssentials();
  }

  start();
})();
