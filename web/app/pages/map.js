// Route checker ("like Google Maps"): search a start and destination, see driving routes and
// every open reported hazard on them, in driving order. #/map?from=lat,lon&to=lat,lon
import { ago, api, CAT, esc, wxIcon } from "/js/core.js";
import { caseIcon, makeMap, SEV_COL } from "/js/map.js";

const PRIO = { high: "P1", medium: "P2", low: "P3" };
const SEV_LABEL = { high: "High risk", medium: "Medium", low: "Low" };
const STORE = "tt.map.last";

export default async function render(el, _params, query) {
  document.body.classList.add("map-mode");
  el.innerHTML = `
    <div class="mp">
      <div id="mpMap" class="mp-map"></div>
      <form class="mp-search" id="search" autocomplete="off" role="search">
        <div class="mp-row"><span class="mp-dot from" aria-hidden="true"></span>
          <input id="from" class="mp-in" placeholder="Choose start, or your location" aria-label="Start">
          <button type="button" class="mp-ic" id="useLoc" title="Use my location" aria-label="Use my location"><i data-lucide="locate-fixed"></i></button></div>
        <div class="mp-row"><span class="mp-dot to" aria-hidden="true"></span>
          <input id="to" class="mp-in" placeholder="Where to?" aria-label="Destination">
          <button type="button" class="mp-ic" id="swap" title="Swap start and destination" aria-label="Swap"><i data-lucide="arrow-down-up"></i></button></div>
        <div class="mp-sugg" id="sugg" role="listbox" hidden></div>
      </form>
      <button class="mp-fab" id="locate" aria-label="Show my location"><i data-lucide="crosshair"></i></button>
      <section class="mp-sheet" id="sheet" aria-live="polite">
        <button class="mp-handle" id="handle" aria-label="Expand or collapse"><span></span></button>
        <div id="sheetBody"><p class="label" style="margin:0">Search a destination to check your route for reported hazards. Tip: long-press the map to drop a pin.</p></div>
      </section>
    </div>`;
  window.lucide?.createIcons({ root: el });
  const $ = (s) => el.querySelector(s);

  const map = makeMap($("#mpMap"), { layers: true, zoom: false });
  L.control.zoom({ position: "bottomright" }).addTo(map);
  const areaLayer = L.layerGroup().addTo(map);
  const routeLayer = L.layerGroup().addTo(map);
  const hzLayer = L.featureGroup().addTo(map);
  let here = null, hereMarker = null;
  let from = null, to = null;                 // {lat, lon, name}
  let result = null, selected = 0, markers = {};

  // ---------------- my location
  const meIcon = L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<div class="me-dot"></div>` });
  function locate(cb, quiet) {
    if (!navigator.geolocation) return quiet || sheetMsg("This browser can't share your location. Type a start place instead.");
    navigator.geolocation.getCurrentPosition((p) => {
      here = { lat: p.coords.latitude, lon: p.coords.longitude, name: "Your location" };
      if (hereMarker) hereMarker.setLatLng([here.lat, here.lon]);
      else hereMarker = L.marker([here.lat, here.lon], { icon: meIcon, interactive: false, zIndexOffset: 500 }).addTo(map);
      cb?.(here);
    }, () => quiet || sheetMsg("Location is blocked. Allow location for this site, or type a start place."),
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
  }

  // ---------------- search suggestions
  let active = null, timer = null, seq = 0;
  const sugg = $("#sugg");
  function showSugg(items, extra = "") {
    sugg.innerHTML = extra + items.map((r, i) => `<button type="button" class="mp-opt" data-i="${i}" role="option">
      <i data-lucide="${/city|town|village|suburb|locality|district/.test(r.type || "") ? "building-2" : "map-pin"}"></i>
      <span><b>${esc(r.name)}</b><small>${esc(r.detail || "")}</small></span></button>`).join("");
    sugg.hidden = !sugg.innerHTML;
    window.lucide?.createIcons({ root: sugg });
    sugg._items = items;
  }
  const hereOpt = () => `<button type="button" class="mp-opt" data-here="1"><i data-lucide="locate-fixed"></i><span><b>Your location</b><small>Use GPS</small></span></button>`;
  function onType(e) {
    active = e.target.id;
    const q = e.target.value.trim();
    clearTimeout(timer);
    if (q.length < 3) { showSugg([], active === "from" ? hereOpt() : ""); return; }
    timer = setTimeout(async () => {
      const my = ++seq, c = map.getCenter();
      try {
        const r = await api(`/api/map/search?q=${encodeURIComponent(q)}&lat=${c.lat}&lon=${c.lng}`);
        if (my === seq) showSugg(r.results, active === "from" ? hereOpt() : "");
      } catch (ex) {
        if (my === seq) { sugg.innerHTML = `<p class="mp-note">${esc(ex.message)}</p>`; sugg.hidden = false; }
      }
    }, 350);
  }
  ["from", "to"].forEach((id) => {
    $(`#${id}`).addEventListener("input", onType);
    $(`#${id}`).addEventListener("focus", (e) => { active = id; if (!e.target.value && id === "from") showSugg([], hereOpt()); });
  });
  sugg.addEventListener("click", (e) => {
    const b = e.target.closest(".mp-opt");
    if (!b) return;
    if (b.dataset.here) { locate((p) => set(active, p)); }
    else { const r = sugg._items[Number(b.dataset.i)]; set(active, { lat: r.lat, lon: r.lon, name: r.name }); }
    sugg.hidden = true;
  });
  document.addEventListener("click", closeSugg);
  function closeSugg(e) { if (!e.target.closest(".mp-search")) sugg.hidden = true; }
  $("#search").addEventListener("submit", (e) => e.preventDefault());

  function set(which, place) {
    if (which === "from") from = place; else to = place;
    $(`#${which}`).value = place ? place.name : "";
    $(`#${which}`).blur();
    if (from && to) go();
    else if (place) map.setView([place.lat, place.lon], 14);
  }
  $("#swap").addEventListener("click", () => { [from, to] = [to, from]; $("#from").value = from?.name || ""; $("#to").value = to?.name || ""; if (from && to) go(); });
  $("#useLoc").addEventListener("click", () => locate((p) => set("from", p)));
  $("#locate").addEventListener("click", () => locate((p) => map.setView([p.lat, p.lon], 15)));

  // long-press / right-click drops a pin
  map.on("contextmenu", async (e) => {
    const which = !to ? "to" : !from ? "from" : "to";
    const place = { lat: e.latlng.lat, lon: e.latlng.lng, name: `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}` };
    set(which, place);
    try { place.name = (await api(`/api/map/reverse?lat=${place.lat}&lon=${place.lon}`)).name; $(`#${which}`).value = place.name; } catch { /* keep coords */ }
  });

  // ---------------- hazards in view (before a route is chosen)
  let areaTimer = null;
  map.on("moveend", () => {
    if (result) return;
    clearTimeout(areaTimer);
    areaTimer = setTimeout(async () => {
      const b = map.getBounds();
      const r = await api(`/api/map/hazards?bbox=${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`).catch(() => null);
      if (!r || result) return;
      areaLayer.clearLayers();
      r.hazards.forEach((h) => pin(areaLayer, h));
    }, 400);
  });

  function popup(h) {
    return `<div class="pop">${h.photo ? `<img src="${esc(h.photo)}?w=480" alt="AI detection photo" loading="lazy">` : ""}
      <h4>${esc(h.label)}</h4>
      <div class="row"><span class="chip prio-${h.priority}"><i></i>${SEV_LABEL[h.severity]}</span><span class="chip"><i></i>${esc(h.status)}</span></div>
      <div class="muted" style="font-size:12px">Reported ${ago(h.first_seen)}${h.weather ? ` · ${esc(h.weather.label)}, ${Math.round(h.weather.temp_c)}°C` : ""}</div>
      ${h.blocking ? `<p style="margin:8px 0 0;color:var(--p1);font-weight:600">Road may be closed</p>` : ""}</div>`;
  }
  function pin(layer, h, big = false) {
    const [lon, lat] = h.centroid;
    const g = L.featureGroup();
    if (h.geometry?.type === "LineString") L.polyline(h.geometry.coordinates.map(([x, y]) => [y, x]), { color: SEV_COL[h.priority], weight: 8, opacity: .8 }).addTo(g);
    L.marker([lat, lon], { icon: caseIcon(h, big ? "big" : ""), riseOnHover: true }).addTo(g);
    g.bindPopup(popup(h), { maxWidth: 260 });
    g.addTo(layer);
    return g;
  }

  // ---------------- routing
  async function go() {
    sheetMsg(`<span class="mp-spin"></span> Checking routes from <b>${esc(from.name)}</b> to <b>${esc(to.name)}</b>…`);
    save();
    try {
      result = await api("/api/map/route", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ origin: [from.lon, from.lat], destination: [to.lon, to.lat] }) });
    } catch (ex) { result = null; routeLayer.clearLayers(); hzLayer.clearLayers(); sheetMsg(esc(ex.message)); return; }
    areaLayer.clearLayers();
    selected = result.recommended;
    draw(true);
  }

  function draw(fit) {
    routeLayer.clearLayers();
    hzLayer.clearLayers();
    markers = {};
    const order = [...result.routes].sort((a, b) => (a.id === selected) - (b.id === selected));   // selected on top
    for (const r of order) {
      const ll = r.coords.map(([x, y]) => [y, x]);
      const on = r.id === selected;
      if (on) L.polyline(ll, { color: "#fff", weight: 10, opacity: 1 }).addTo(routeLayer);
      L.polyline(ll, { color: on ? (r.blocking ? "#ef4444" : "#1a73e8") : "#9aa0a6", weight: on ? 6 : 5, opacity: on ? 1 : .8 })
        .on("click", () => { selected = r.id; draw(false); }).addTo(routeLayer);
    }
    L.circleMarker([from.lat, from.lon], { radius: 7, color: "#fff", weight: 3, fillColor: "#10b981", fillOpacity: 1 }).addTo(routeLayer);
    L.marker([to.lat, to.lon], { icon: L.divIcon({ className: "", iconSize: [34, 46], iconAnchor: [17, 44], html: `<div class="pin"></div>` }) }).addTo(routeLayer);
    const r = result.routes.find((x) => x.id === selected);
    r.hazards.forEach(({ id }) => { markers[id] = pin(hzLayer, result.hazards[id], true); });
    if (fit) map.fitBounds(L.latLngBounds(r.coords.map(([x, y]) => [y, x])), { paddingTopLeft: [20, 150], paddingBottomRight: [20, 260] });
    sheet(r);
  }

  function chip(r) {
    const n = r.hazards.length;
    const badges = [r.id === result.recommended ? `<span class="mp-badge safe">Safest</span>` : "", r.id === result.fastest ? `<span class="mp-badge">Fastest</span>` : ""].join("");
    return `<button class="mp-route ${r.id === selected ? "on" : ""}" data-route="${r.id}">
      <span class="mp-route-h"><b>${fmtMin(r.minutes)}</b><span>${r.km} km</span></span>
      <span class="${n ? (r.blocking || r.counts.high ? "bad" : "warn") : "ok"}">${n ? `⚠ ${n} hazard${n === 1 ? "" : "s"}` : "✓ No reported hazards"}</span>
      ${badges ? `<span class="mp-badges">${badges}</span>` : ""}</button>`;
  }

  function sheet(r) {
    const hz = r.hazards.map(({ id, along_km }) => ({ ...result.hazards[id], along_km }));
    const g = `https://www.google.com/maps/dir/?api=1&origin=${from.lat},${from.lon}&destination=${to.lat},${to.lon}&travelmode=driving`;
    $("#sheetBody").innerHTML = `
      <div class="mp-routes">${result.routes.map(chip).join("")}</div>
      ${r.blocking ? `<p class="err-box" style="margin:10px 0 0">A hazard on this route may block the road${result.routes.some((x) => !x.blocking) ? ". Another route avoids it." : "."}</p>` : ""}
      <div class="mp-head"><b>${hz.length ? `${hz.length} reported hazard${hz.length === 1 ? "" : "s"} on this route` : "No reported hazards on this route"}</b>
        <a class="btn sm primary" href="${g}" target="_blank" rel="noopener"><i data-lucide="navigation"></i>Start in Google Maps</a></div>
      <div class="mp-list">${hz.map((h) => `<button class="mp-hz" data-hz="${esc(h.id)}">
          ${h.photo ? `<img src="${esc(h.photo)}?w=160" alt="" loading="lazy" width="64" height="48">` : `<span class="tile t-${h.category}"><i data-lucide="${CAT[h.category]?.icon || "triangle-alert"}"></i></span>`}
          <span class="mp-hz-t"><span class="mp-hz-h"><b>${esc(h.label)}</b><span class="chip prio-${h.priority}"><i></i>${SEV_LABEL[h.severity]}</span></span>
            <small><b>${h.along_km < 0.05 ? "At the start" : `In ${h.along_km < 1 ? `${Math.round(h.along_km * 1000)} m` : `${h.along_km.toFixed(1)} km`}`}</b> · ${esc(h.status)} · seen ${ago(h.first_seen)}</small>
            ${h.weather ? `<small>${wxIcon(h.weather)} ${esc(h.weather.label)}, ${Math.round(h.weather.temp_c)}°C at detection</small>` : ""}
            ${h.blocking ? `<small style="color:var(--p1);font-weight:600">Road may be closed</small>` : ""}</span></button>`).join("")
        || `<div class="mp-clear"><i data-lucide="shield-check"></i><span>Clear so far: no open hazards have been reported along this road. Drive safe!</span></div>`}</div>`;
    window.lucide?.createIcons({ root: $("#sheetBody") });
    $("#sheet").classList.add("has-route");
  }
  const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);
  function sheetMsg(html) { $("#sheetBody").innerHTML = `<p class="label" style="margin:0">${html}</p>`; }

  $("#sheetBody").addEventListener("click", (e) => {
    const rb = e.target.closest("[data-route]"), hb = e.target.closest("[data-hz]");
    if (rb) { selected = Number(rb.dataset.route); draw(false); }
    if (hb && markers[hb.dataset.hz]) {
      const m = markers[hb.dataset.hz];
      map.flyTo(m.getBounds().getCenter(), 17, { duration: .8 });
      setTimeout(() => m.openPopup(), 850);
      $("#sheet").classList.remove("open");
    }
  });
  // bottom sheet: tap the handle or swipe to expand / collapse
  const sh = $("#sheet");
  $("#handle").addEventListener("click", () => sh.classList.toggle("open"));
  let y0 = null;
  sh.addEventListener("touchstart", (e) => { y0 = e.touches[0].clientY; }, { passive: true });
  sh.addEventListener("touchend", (e) => {
    if (y0 == null) return;
    const dy = e.changedTouches[0].clientY - y0;
    if (dy < -40) sh.classList.add("open"); else if (dy > 40) sh.classList.remove("open");
    y0 = null;
  });

  // ---------------- state: URL > last trip > my location
  function save() {
    const q = `from=${from.lat.toFixed(5)},${from.lon.toFixed(5)}&to=${to.lat.toFixed(5)},${to.lon.toFixed(5)}`;
    history.replaceState(null, "", `#/map?${q}`);
    try { localStorage.setItem(STORE, JSON.stringify({ from, to })); } catch { /* storage blocked */ }
  }
  const parse = (s) => { const [lat, lon] = (s || "").split(",").map(Number); return Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null; };
  const qf = parse(query.get("from")), qt = parse(query.get("to"));
  if (qf && qt) {
    from = { ...qf, name: `${qf.lat.toFixed(4)}, ${qf.lon.toFixed(4)}` };
    to = { ...qt, name: `${qt.lat.toFixed(4)}, ${qt.lon.toFixed(4)}` };
    $("#from").value = from.name; $("#to").value = to.name;
    go();
    for (const w of ["from", "to"]) {
      const p = w === "from" ? from : to;
      api(`/api/map/reverse?lat=${p.lat}&lon=${p.lon}`).then((r) => { p.name = r.name; $(`#${w}`).value = r.name; }).catch(() => {});
    }
  } else {
    let last = null;
    try { last = JSON.parse(localStorage.getItem(STORE) || "null"); } catch { /* ignore */ }
    if (last?.to) { to = last.to; $("#to").value = to.name; }
    locate((p) => {
      if (!from) { from = p; $("#from").value = p.name; }
      if (from && to) go(); else map.setView([p.lat, p.lon], 14);
    }, true);
    map.fire("moveend");
  }

  return () => { document.body.classList.remove("map-mode"); document.removeEventListener("click", closeSugg); map.remove(); };
}
