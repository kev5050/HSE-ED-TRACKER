/* ED Today - renders data/latest.json, data/history.json and data/locations.json */
(() => {
  "use strict";

  const PRESSURE = {
    "much busier than usual": { cls: "p-much", text: "Much busier than usual", rank: 4 },
    "busier than usual": { cls: "p-busier", text: "Busier than usual", rank: 3 },
    "usual": { cls: "p-usual", text: "About usual", rank: 2 },
    "quieter than usual": { cls: "p-quieter", text: "Quieter than usual", rank: 1 },
  };
  const NO_HISTORY = { cls: "p-none", text: "Not enough history yet to compare", rank: 0 };

  const $ = (s) => document.querySelector(s);
  const list = $("#list");
  const tpl = $("#row-tpl");
  let hospitals = [];
  let history = {};
  let sortBy = "waiting";
  let here = null;
  let firstRender = true;

  const fmtDate = (iso, opts) =>
    new Date(iso + "T12:00:00").toLocaleDateString("en-IE", opts);

  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  async function load() {
    try {
      const [latest, hist, locs] = await Promise.all([
        getJSON("data/latest.json"),
        getJSON("data/history.json").catch(() => ({ hospitals: {}, national: [] })),
        getJSON("data/locations.json").catch(() => ({})),
      ]);
      history = hist.hospitals || {};
      hospitals = latest.hospitals
        .filter((h) => h.has_ed && h.total_trolleys !== null)
        .map((h) => ({ ...h, ...(locs[h.id] || {}) }));
      renderLede(latest, hist.national || []);
      render();
    } catch (err) {
      $("#headline").textContent = "Today's figures didn't load.";
      $("#subline").textContent = "Check your connection and refresh the page. If it keeps happening, the HSE report may be late.";
      console.error(err);
    }
  }

  function renderLede(latest, national) {
    const total = latest.national.total_trolleys;
    const day = fmtDate(latest.report_date, { weekday: "long", day: "numeric", month: "long" });
    const h1 = $("#headline");
    h1.textContent = "";
    const big = document.createElement("span");
    big.className = "big";
    big.textContent = `${total.toLocaleString("en-IE")} people`;
    h1.append(big, ` were on trolleys waiting for a hospital bed at 8am on ${day}.`);

    const prior = national.filter(([d]) => d < latest.report_date).slice(-28).map(([, v]) => v);
    if (prior.length >= 7) {
      const avg = Math.round(prior.reduce((a, b) => a + b, 0) / prior.length);
      const diff = total - avg;
      $("#subline").textContent =
        Math.abs(diff) < avg * 0.05
          ? `That's about the same as the daily average for the last ${prior.length} days (${avg}).`
          : `That's ${Math.abs(diff)} ${diff > 0 ? "more" : "fewer"} than the daily average for the last ${prior.length} days (${avg}).`;
    }

    const ageDays = Math.round((Date.now() - new Date(latest.report_date + "T08:00:00")) / 864e5);
    const stamp = $("#stamp");
    stamp.textContent = `From the HSE report taken at ${latest.snapshot_time} on ${fmtDate(latest.report_date, { weekday: "short", day: "numeric", month: "short" })}. The HSE publishes one report a day.`;
    if (ageDays >= 2) {
      stamp.textContent += ` This is ${ageDays} days old because no newer report has been published.`;
      stamp.classList.add("stale");
    }
  }

  // ---------- helpers ----------
  const pressureOf = (h) => PRESSURE[h.pressure] || NO_HISTORY;

  function distanceKm(a, b) {
    const R = 6371, rad = Math.PI / 180;
    const dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  }

  function sorted(items) {
    const by = {
      waiting: (a, b) => b.total_trolleys - a.total_trolleys,
      "vs-usual": (a, b) => {
        const ra = a.usual_total_trolleys ? a.total_trolleys / Math.max(a.usual_total_trolleys, 1) : -1;
        const rb = b.usual_total_trolleys ? b.total_trolleys / Math.max(b.usual_total_trolleys, 1) : -1;
        return rb - ra;
      },
      nearest: (a, b) => (a.km ?? 1e9) - (b.km ?? 1e9),
      name: (a, b) => a.name.localeCompare(b.name),
    }[sortBy];
    return [...items].sort((a, b) => by(a, b) || a.name.localeCompare(b.name));
  }

  function matches(h, q) {
    if (!q) return true;
    const hay = `${h.name} ${h.town || ""} ${h.region}`.toLowerCase();
    return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
  }

  // ---------- render ----------
  function render() {
    const q = $("#search").value.trim();
    const items = sorted(hospitals.filter((h) => matches(h, q)));
    const max = Math.max(10, ...hospitals.map((h) => Math.max(h.total_trolleys, h.usual_total_trolleys || 0)));
    const scale = Math.ceil(max / 10) * 10;

    const open = new Set([...list.querySelectorAll('.row-head[aria-expanded="true"]')].map((b) => b.dataset.id));
    list.replaceChildren(...items.map((h, i) => row(h, scale, i, open.has(h.id))));
    list.classList.toggle("animate", firstRender);
    firstRender = false;
    $("#empty").hidden = items.length > 0;
  }

  function row(h, scale, i, isOpen) {
    const li = tpl.content.firstElementChild.cloneNode(true);
    const p = pressureOf(h);
    const head = li.querySelector(".row-head");
    head.dataset.id = h.id;

    li.querySelector(".row-name").textContent = h.name;
    li.querySelector(".num").textContent = h.total_trolleys;

    const where = li.querySelector(".row-where");
    const bits = [h.town || h.region.replace(/^HSE /, "")];
    if (h.km !== undefined) bits.push(`${h.km < 10 ? h.km.toFixed(1) : Math.round(h.km)} km away`);
    where.textContent = bits.join(", ");
    if (h.children) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "Children only";
      where.append(tag);
    }

    const bar = li.querySelector(".bar");
    bar.style.setProperty("--max", scale);
    bar.classList.add(p.cls);
    const fill = li.querySelector(".fill");
    fill.style.width = `${(h.total_trolleys / scale) * 100}%`;
    fill.style.setProperty("--i", i);
    const usual = li.querySelector(".usual");
    if (h.usual_total_trolleys !== null) usual.style.left = `${(h.usual_total_trolleys / scale) * 100}%`;
    else usual.remove();

    const status = li.querySelector(".row-status");
    status.classList.add(p.cls);
    status.textContent = p.text;
    if (h.usual_total_trolleys !== null) {
      const u = document.createElement("span");
      u.className = "usually";
      u.textContent = `, usually ${Math.round(h.usual_total_trolleys)}`;
      status.append(u);
    }

    head.setAttribute("aria-label",
      `${h.name}: ${h.total_trolleys} people waiting on trolleys. ${p.text}` +
      (h.usual_total_trolleys !== null ? `, usually ${Math.round(h.usual_total_trolleys)}.` : "."));

    const detail = li.querySelector(".row-detail");
    head.addEventListener("click", () => {
      const nowOpen = head.getAttribute("aria-expanded") !== "true";
      head.setAttribute("aria-expanded", String(nowOpen));
      detail.hidden = !nowOpen;
      if (nowOpen && !detail.dataset.built) buildDetail(detail, h);
    });
    if (isOpen) {
      head.setAttribute("aria-expanded", "true");
      detail.hidden = false;
      buildDetail(detail, h);
    }
    return li;
  }

  function buildDetail(detail, h) {
    detail.dataset.built = "1";
    const series = (history[h.id] || []).filter(([, v]) => v !== null);
    const svg = detail.querySelector(".spark");
    const range = detail.querySelector(".spark-range");
    if (series.length < 2) {
      detail.querySelector(".trend").remove();
    } else {
      drawSpark(svg, series, h);
      range.innerHTML = "";
      const a = document.createElement("span");
      const b = document.createElement("span");
      a.textContent = fmtDate(series[0][0], { day: "numeric", month: "short" });
      b.textContent = fmtDate(series[series.length - 1][0], { day: "numeric", month: "short" });
      range.append(a, b);
    }

    const dl = detail.querySelector(".breakdown");
    const rows = [
      ["Waiting in the emergency department", h.ed_trolleys],
      ["Waiting on a ward", h.ward_trolleys],
      ["Waiting more than 24 hours", h.waiting_over_24h],
      ["Aged 75 or over, waiting more than 24 hours", h.over75_over_24h],
      ["Well enough to leave but still in a bed (midnight count)", h.delayed_transfers],
    ];
    for (const [label, v] of rows) {
      if (v === null || v === undefined) continue;
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = v;
      dl.append(dt, dd);
    }

    const dir = detail.querySelector(".directions");
    dir.href = h.lat
      ? `https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lon}`
      : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(h.name + " Ireland")}`;
    dir.textContent = "Get directions";
    dir.setAttribute("aria-label", `Get directions to ${h.name} (opens Google Maps)`);
  }

  function drawSpark(svg, series, h) {
    const NS = "http://www.w3.org/2000/svg";
    const W = 600, H = 72, n = series.length;
    const max = Math.max(...series.map(([, v]) => v), h.usual_total_trolleys || 0, 1);
    const slot = W / n, bw = Math.max(2, slot * 0.7);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-label",
      `Daily trolley counts from ${series[0][1]} to ${series[n - 1][1]}, highest ${Math.max(...series.map(([, v]) => v))}.`);
    series.forEach(([day, v], i) => {
      const dow = new Date(day + "T12:00:00").getDay();
      if (dow === 0 || dow === 6) {          // shade weekends: they're usually quieter
        const bg = document.createElementNS(NS, "rect");
        bg.setAttribute("class", "wk");
        bg.setAttribute("x", i * slot); bg.setAttribute("y", 0);
        bg.setAttribute("width", slot); bg.setAttribute("height", H);
        svg.append(bg);
      }
      const r = document.createElementNS(NS, "rect");
      const bh = Math.max(1, (v / max) * (H - 4));
      r.setAttribute("class", i === n - 1 ? "b today" : "b");
      r.setAttribute("x", i * slot + (slot - bw) / 2);
      r.setAttribute("y", H - bh);
      r.setAttribute("width", bw);
      r.setAttribute("height", bh);
      const t = document.createElementNS(NS, "title");
      t.textContent = `${fmtDate(day, { weekday: "short", day: "numeric", month: "short" })}: ${v}`;
      r.append(t);
      svg.append(r);
    });
  }

  // ---------- controls ----------
  function setSort(next) {
    sortBy = next;
    document.querySelectorAll(".sort button").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.sort === next)));
    render();
  }
  document.querySelectorAll(".sort button").forEach((b) =>
    b.addEventListener("click", () => setSort(b.dataset.sort)));

  $("#search").addEventListener("input", render);

  $("#locate").addEventListener("click", () => {
    const btn = $("#locate"), msg = $("#locate-msg");
    if (!navigator.geolocation) {
      msg.textContent = "Your browser can't share your location. Search by town instead.";
      return;
    }
    btn.setAttribute("aria-busy", "true");
    msg.textContent = "Finding your location…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        btn.removeAttribute("aria-busy");
        here = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        hospitals.forEach((h) => { if (h.lat) h.km = distanceKm(here, h); });
        msg.textContent = "Sorted by distance as the crow flies. Your location stays on your device.";
        document.querySelector('[data-sort="nearest"]').hidden = false;
        setSort("nearest");
      },
      () => {
        btn.removeAttribute("aria-busy");
        msg.textContent = "Location is turned off for this site. Search by town instead, or allow location in your browser settings.";
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }
    );
  });

  load();
})();
