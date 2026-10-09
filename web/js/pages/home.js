// Home: every analysed drive on one map, introduced by an animated tour.
// The tour flies to each location, grows the newest drive there from start to end, drops each
// hazard pin as the route reaches it, lands an end pin, then zooms out to show everything.
import { $, api, date, esc, healthOf, store, CAT, PRIO_OF } from "../core.js";
import { addCase, animateRoute, drawRoute, endIcon, haversine, headIcon, makeMap, startIcon } from "../map.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const GROUP_KM = 3;

export default async function render(el) {
  const [routes, { cases }] = await Promise.all([api("/api/routes"), store.cases(true)]);
  const caseOf = Object.fromEntries(cases.map((c) => [c.id, c]));
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- data shaping
  for (const r of routes) {
    r.ll = r.coords.map(([x, y]) => [y, x]);
    r.items = r.hazard_list.map((h) => {
      const c = caseOf[`${r.id}/${h.id}`];
      return { ...h, run_id: r.id, hazard_id: h.id, number: c?.number || h.id, priority: c?.priority || PRIO_OF[h.severity] || "P3",
        status: c?.status || "new", label: CAT[h.category]?.label || h.category,
        snapshot: h.snapshot ? `/runs/${encodeURIComponent(r.id)}/${h.snapshot}` : null };
    });
    r.health = healthOf(r.hazard_list.map((h) => h.severity), r.route_km);
  }
  // drives that start within GROUP_KM of each other are one location (same road, re-processed)
  const groups = [];
  for (const r of routes.slice().reverse()) {                         // newest first
    const g = groups.find((x) => haversine(x.anchor, r.ll[0]) < GROUP_KM * 1000);
    if (g) g.routes.push(r); else groups.push({ anchor: r.ll[0], place: r.place, routes: [r] });
  }
  for (const g of groups) {
    g.latest = g.routes[0];
    g.bounds = L.latLngBounds(g.routes.flatMap((r) => r.ll));
    // one pin per real-world hazard: drop duplicates seen again on re-processed drives
    g.items = [];
    for (const r of g.routes) for (const i of r.items) {
      const dup = g.items.find((x) => x.category === i.category && haversine([x.centroid[1], x.centroid[0]], [i.centroid[1], i.centroid[0]]) < 15);
      if (!dup) g.items.push(i);
      else if (i.priority < dup.priority) Object.assign(dup, i);
    }
    // where along the newest route each hazard is reached
    for (const i of g.items) {
      let best = 0, bd = Infinity;
      g.latest.ll.forEach((p, k) => { const d = haversine(p, [i.centroid[1], i.centroid[0]]); if (d < bd) { bd = d; best = k; } });
      i.at = best;
    }
  }
  const totals = {
    drives: routes.length,
    km: routes.reduce((s, r) => s + (r.route_km || 0), 0),
    hazards: groups.reduce((s, g) => s + g.items.length, 0),
    critical: groups.reduce((s, g) => s + g.items.filter((i) => i.priority === "P1" && i.status !== "resolved").length, 0),
  };

  // ---------- markup
  el.innerHTML = `
    <div class="home-map map-card">
      <div class="map" id="homeMap"></div>
      <aside class="route-panel" id="routePanel" aria-label="Survey routes">
        <header><div><h2>Survey routes</h2><span class="label">${groups.length} locations · ${routes.length} drives</span></div>
          <button class="icon-btn" id="panelToggle" aria-label="Collapse panel" aria-expanded="true"><i data-lucide="panel-left-close"></i></button></header>
        <ol class="loc-list" id="locList">${groups.map((g, gi) => `
          <li class="loc" data-g="${gi}">
            <button class="loc-head" data-g="${gi}"><span class="loc-ic"><i data-lucide="map-pin"></i></span>
              <span><b>${esc(g.place)}</b><span class="label">${g.routes.length} drive${g.routes.length > 1 ? "s" : ""} · ${g.items.length} hazards</span></span>
              <i data-lucide="play"></i></button>
            <ul class="drive-list">${g.routes.map((r) => `
              <li><a href="#/command/${encodeURIComponent(r.id)}" title="Open in Command">
                <span class="hdot ${r.health.tone}" title="Health ${r.health.grade}"></span>
                <span class="d">${esc(r.id)}</span><span class="label">${r.route_km} km · ${r.hazards}</span></a></li>`).join("")}</ul>
          </li>`).join("") || `<li class="pop-empty">No drives yet. Use New drive to process one.</li>`}</ol>
      </aside>
      <div class="kpi-chips" aria-label="Totals">
        <span class="kchip"><b data-k="drives">0</b> drives</span>
        <span class="kchip"><b data-k="km">0</b> km surveyed</span>
        <span class="kchip"><b data-k="hazards">0</b> hazards</span>
        <span class="kchip crit"><b data-k="critical">0</b> critical</span>
      </div>
      <div class="tour-card" id="tourCard" hidden aria-live="polite"></div>
      <div class="tour-controls">
        <button class="btn sm" id="replay"><i data-lucide="rotate-ccw"></i>Replay tour</button>
        <button class="btn sm primary" id="skip"><i data-lucide="skip-forward"></i>Skip</button>
      </div>
      <div class="map-legend" aria-hidden="true">
        <span><i style="background:#10b981;box-shadow:0 0 0 2px #fff,0 0 0 3px #10b981"></i>Start</span>
        <span><i style="background:#111;border-radius:50% 50% 50% 0;transform:rotate(-45deg)"></i>End</span>
        <span><i style="background:#ef4444"></i>P1</span><span><i style="background:#f59e0b"></i>P2</span><span><i style="background:#10b981"></i>P3</span>
      </div>
    </div>`;

  const map = makeMap($("#homeMap", el));
  const base = L.layerGroup().addTo(map), anim = L.layerGroup().addTo(map), pins = L.featureGroup().addTo(map);
  const allBounds = L.latLngBounds(routes.flatMap((r) => r.ll));
  const pad = () => (innerWidth > 900 ? { paddingTopLeft: [380, 90], paddingBottomRight: [60, 90] } : { padding: [40, 40] });
  let tourId = 0, running = null;

  const setKpi = (vals) => Object.entries(vals).forEach(([k, v]) => {
    const n = $(`[data-k="${k}"]`, el);
    if (n) n.textContent = k === "km" ? v.toFixed(1) : Math.round(v);
  });
  function countUp(ms = 900) {
    const t0 = performance.now();
    const step = (now) => {
      const f = Math.min(1, (now - t0) / ms);
      setKpi(Object.fromEntries(Object.entries(totals).map(([k, v]) => [k, v * (1 - (1 - f) ** 3)])));
      if (f < 1 && alive) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function fly(bounds, duration = 1.2) {
    return new Promise((resolve) => {
      if (!bounds?.isValid()) return resolve();
      const done = () => { clearTimeout(t); resolve(); };
      const t = setTimeout(done, duration * 1000 + 600);
      map.once("moveend", done);
      map.flyToBounds(bounds, { ...pad(), maxZoom: 17, duration });
    });
  }

  // final state: every drive drawn, start/end markers, one pin per hazard, location labels
  function drawFinal() {
    base.clearLayers(); anim.clearLayers(); pins.clearLayers();
    for (const g of groups) {
      for (const r of g.routes.slice().reverse()) drawRoute(base, r.coords, { startDot: false });
      L.marker(g.latest.ll[0], { icon: startIcon(), zIndexOffset: 500 }).bindTooltip("Start").addTo(base);
      L.marker(g.latest.ll.at(-1), { icon: endIcon(), zIndexOffset: 600 })
        .bindTooltip(`<b>${esc(g.place)}</b><br>${g.routes.length} drive${g.routes.length > 1 ? "s" : ""} · ${g.items.length} hazards`, { direction: "top", offset: [0, -40] })
        .addTo(base);
      g.items.forEach((i) => addCase(pins, i));
    }
    $("#tourCard", el).hidden = true;
  }

  function tourCard(g, gi, r, kmDone, found) {
    const card = $("#tourCard", el);
    card.innerHTML = `
      <div class="tc-top"><span class="label">Stop ${gi + 1} of ${groups.length}</span>
        <span class="dots">${groups.map((_, k) => `<i class="${k < gi ? "done" : k === gi ? "on" : ""}"></i>`).join("")}</span></div>
      <b class="place">${esc(g.place)}</b>
      <span class="label">${esc(r.id)} · ${date(r.video_start)}</span>
      <div class="tc-live"><div><b>${kmDone.toFixed(2)}</b><span class="label">km driven</span></div>
        <div><b>${found}</b><span class="label">hazards found</span></div>
        <div><b>${g.routes.length}</b><span class="label">drive${g.routes.length > 1 ? "s" : ""} here</span></div></div>`;
    card.hidden = false;
  }

  // animate one location: newest drive grows from start to end, pins drop as it passes them
  async function playGroup(g, gi, id) {
    await fly(g.bounds);
    if (id !== tourId) return;
    for (const r of g.routes.slice(1)) drawRoute(base, r.coords, { faint: true });   // earlier drives on the same road
    const r = g.latest;
    L.marker(r.ll[0], { icon: startIcon(), zIndexOffset: 500 }).bindTooltip("Start").addTo(anim);
    L.polyline([r.ll[0]], { color: "#fff", weight: 11, opacity: .85, lineCap: "round", lineJoin: "round" }).addTo(anim);
    const casing = anim.getLayers().at(-1);
    const line = L.polyline([r.ll[0]], { color: "#111", weight: 6, lineCap: "round", lineJoin: "round" }).addTo(anim);
    const head = L.marker(r.ll[0], { icon: headIcon(), zIndexOffset: 900 }).addTo(anim);
    const pending = g.items.slice().sort((a, b) => a.at - b.at);
    let found = 0;
    tourCard(g, gi, r, 0, 0);
    running = animateRoute(line, r.ll, {
      duration: Math.min(4200, 1800 + r.ll.length * 6),
      onProgress: (i, tip, f) => {
        casing.setLatLngs(line.getLatLngs());
        head.setLatLng(tip);
        while (pending.length && pending[0].at <= i) {
          addCase(pins, pending.shift(), { drop: true });
          found++;
        }
        tourCard(g, gi, r, (r.route_km || 0) * f, found);
      },
    });
    const ok = await running;
    if (!ok || id !== tourId) return;
    pending.forEach((i) => addCase(pins, i, { drop: true }));
    anim.removeLayer(head);
    L.marker(r.ll.at(-1), { icon: endIcon(), zIndexOffset: 600 }).bindTooltip(`End · ${esc(g.place)}`).addTo(anim);
    await sleep(900);
  }

  async function tour(only = null) {
    const id = ++tourId;
    running?.cancel?.();
    if (reduced) { drawFinal(); setKpi(totals); fit(); return; }
    base.clearLayers(); anim.clearLayers(); pins.clearLayers();
    if (only == null) {
      map.fitBounds(allBounds, { ...pad(), maxZoom: 15 });
      countUp();
      groups.forEach((g) => L.marker(g.latest.ll[0], { icon: startIcon() }).addTo(anim));
      await sleep(1100);
    }
    const list = only == null ? groups.map((g, gi) => [g, gi]) : [[groups[only], only]];
    for (const [g, gi] of list) {
      if (id !== tourId) return;
      anim.clearLayers();
      markActive(gi);
      await playGroup(g, gi, id);
    }
    if (id !== tourId) return;
    running = null;
    markActive(-1);
    drawFinal();
    if (only == null) await fly(allBounds, 1.4);
  }
  const fit = () => { if (allBounds.isValid()) map.fitBounds(allBounds, { ...pad(), maxZoom: 15 }); };
  function stop(final = true) {
    tourId++;
    running?.cancel?.();
    running = null;
    markActive(-1);
    if (final) { drawFinal(); setKpi(totals); }
  }
  function markActive(gi) {
    el.querySelectorAll(".loc").forEach((li) => li.classList.toggle("on", Number(li.dataset.g) === gi));
  }

  // ---------- controls
  let alive = true;
  $("#skip", el).addEventListener("click", () => { stop(); fit(); });
  $("#replay", el).addEventListener("click", () => tour());
  $("#locList", el).addEventListener("click", (e) => {
    const b = e.target.closest(".loc-head");
    if (!b) return;
    stop(false); drawFinal(); setKpi(totals);
    tour(Number(b.dataset.g));
  });
  $("#panelToggle", el).addEventListener("click", (e) => {
    const p = $("#routePanel", el), collapsed = p.classList.toggle("collapsed");
    e.currentTarget.setAttribute("aria-expanded", !collapsed);
  });
  map.on("dragstart", () => { if (running) stop(true); });        // user takes the camera: finish instantly

  if (!routes.length) { setKpi(totals); return () => { alive = false; map.remove(); }; }
  tour();
  return () => { alive = false; tourId++; running?.cancel?.(); map.remove(); };
}
