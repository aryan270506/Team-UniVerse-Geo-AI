// Map styling: full-colour basemaps (streets / dark / satellite), round colour-coded hazard
// markers (red = P1 high, amber = P2 medium, green = P3 low), blue route, green dashed detours.
// Kept deliberately more colourful than the Swiss UI around it so the map is easy to read.
import { CAT, coord, esc, prioTag, statusTag, caseHref } from "./core.js";

export const SEV_COL = { P1: "#ef4444", P2: "#f59e0b", P3: "#10b981" };
export const ROUTE_COL = "#111111";

export function makeMap(el, { zoom = true, layers = true } = {}) {
  const map = L.map(el, { zoomControl: false, attributionControl: true }).setView([20.5, 78.9], 5);
  // soft colour: standard OSM tiles, desaturated in CSS (.leaflet-tile-pane) to sit calmly in the UI
  const base = {
    Map: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
    Dark: L.layerGroup([
      L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 20, maxNativeZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap" }),
      L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 20, maxNativeZoom: 16 }),
    ]),
    Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 20, maxNativeZoom: 19, attribution: "Imagery &copy; Esri" }),
  };
  base.Map.addTo(map);
  if (layers) L.control.layers(base, null, { position: "topright" }).addTo(map);
  if (zoom) L.control.zoom({ position: "topright" }).addTo(map);
  L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
  return map;
}

export function caseIcon(c, extra = "") {
  const cls = c.status === "resolved" ? "resolved" : c.priority;
  return L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -14],
    html: `<div class="hz-icon ${cls} ${extra}">${CAT[c.category]?.glyph ?? "?"}</div>` });
}

export function casePopup(c) {
  return `<div class="pop">
    ${c.snapshot ? `<img src="${esc(c.snapshot)}" alt="Detection frame">` : ""}
    <h4>${esc(c.number || c.hazard_id)} · ${esc(c.label)}</h4>
    <div class="row">${prioTag(c.priority)} ${c.status ? statusTag(c.status) : ""}</div>
    <div class="muted" style="font-size:12px">${coord(c.centroid)}${c.extent_m >= 15 ? ` · ${c.extent_m} m stretch` : ""}</div>
    ${c.run_id ? `<p style="margin-top:8px"><a class="link-btn" href="${caseHref(c)}">Open case</a></p>` : ""}</div>`;
}

export function addCase(layer, c, { popup = true, drop = false } = {}) {
  const [lon, lat] = c.centroid;
  const g = L.featureGroup();
  if (c.geometry?.type === "LineString") {
    L.polyline(c.geometry.coordinates.map(([x, y]) => [y, x]),
      { color: c.status === "resolved" ? "#9aa0a6" : SEV_COL[c.priority], weight: 8, opacity: .8, lineCap: "round" }).addTo(g);
  }
  L.marker([lat, lon], { icon: caseIcon(c, drop ? "drop" : ""), riseOnHover: true, title: `${c.number || c.hazard_id} ${c.label}` }).addTo(g);
  if (popup) g.bindPopup(casePopup(c), { maxWidth: 280 });
  g.addTo(layer);
  return g;
}

export function drawRoute(layer, lonlat, { faint = false, startDot = true } = {}) {
  const ll = lonlat.map(([x, y]) => [y, x]);
  if (ll.length < 2) return null;
  if (faint) return L.polyline(ll, { color: ROUTE_COL, weight: 3, opacity: .35, dashArray: "6 8" }).addTo(layer);
  L.polyline(ll, { color: "#ffffff", weight: 11, opacity: .85, lineCap: "round", lineJoin: "round" }).addTo(layer);   // soft casing
  const line = L.polyline(ll, { color: ROUTE_COL, weight: 6, opacity: .95, lineCap: "round", lineJoin: "round" }).addTo(layer);
  if (startDot) L.circleMarker(ll[0], { radius: 6, color: "#fff", weight: 3, fillColor: "#111", fillOpacity: 1 }).bindTooltip("Drive start").addTo(layer);
  return line;
}

// The reference's black location pin with a soft pulse, for the selected case.
export const pinIcon = () => L.divIcon({ className: "", iconSize: [34, 46], iconAnchor: [17, 44], popupAnchor: [0, -40],
  html: `<div class="pin"><span class="pin-pulse"></span></div>` });

export function haversine(a, b) {      // [lat, lon] -> metres
  const R = 6371000, r = Math.PI / 180;
  const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// Colour the route next to hazards: P1 red, P2 amber, P3 green.
const COND = SEV_COL;
const RANK = { P1: 3, P2: 2, P3: 1 };
export function renderCondition(layer, routeLL, cases) {
  layer.clearLayers();
  if (routeLL.length < 2 || !cases.length) return;
  const pts = cases.map((c) => ({ p: c.priority, at: [c.centroid[1], c.centroid[0]], reach: Math.max(30, (c.extent_m || 0) / 2 + 20) }));
  let run = null;
  const flush = () => { if (run && run.ll.length > 1) L.polyline(run.ll, { color: COND[run.p], weight: 9, opacity: .75, lineCap: "round", interactive: false }).addTo(layer); };
  for (let i = 1; i < routeLL.length; i++) {
    const mid = [(routeLL[i - 1][0] + routeLL[i][0]) / 2, (routeLL[i - 1][1] + routeLL[i][1]) / 2];
    let worst = null;
    for (const q of pts) if (haversine(mid, q.at) <= q.reach && (!worst || RANK[q.p] > RANK[worst])) worst = q.p;
    if (worst && run && run.p === worst) run.ll.push(routeLL[i]);
    else { flush(); run = worst ? { p: worst, ll: [routeLL[i - 1], routeLL[i]] } : null; }
  }
  flush();
}

// Closures (⛔) and detours (green dashed). Returns note rows for an overlay panel.
export function renderClosures(layer, closures) {
  layer.clearLayers();
  const notes = [];
  for (const c of closures || []) {
    const [lon, lat] = c.point;
    L.marker([lat, lon], { icon: L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [-6, 36], html: `<div class="closed-mk" title="Road closed">⛔</div>` }), zIndexOffset: 400 })
      .bindTooltip(`Road closed · ${esc(CAT[c.category]?.label ?? c.category)} (${esc(c.hazard_id)})`).addTo(layer);
    if (c.detour) {
      L.polyline(c.detour.coords.map(([x, y]) => [y, x]), { color: "#10b981", weight: 5, opacity: .9, dashArray: "10 8" })
        .bindTooltip(`Detour ${c.detour.km} km · ~${c.detour.minutes} min`, { sticky: true }).addTo(layer);
    }
    notes.push(c);
  }
  return notes;
}

export const vehicleIcon = (heading) => L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 15],
  html: `<div class="vehicle"><div class="arrow" style="transform: rotate(${heading ?? 0}deg)${heading == null ? ";opacity:0" : ""}"></div></div>` });

// ---------- route journey animation (Home tour)
export const startIcon = () => L.divIcon({ className: "", iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="start-dot"></div>` });
export const headIcon = () => L.divIcon({ className: "", iconSize: [16, 16], iconAnchor: [8, 8], html: `<div class="head-dot"></div>` });
export const endIcon = () => L.divIcon({ className: "", iconSize: [34, 46], iconAnchor: [17, 44], popupAnchor: [0, -40], html: `<div class="pin land"></div>` });

const ease = (t) => (t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

/** Grow `line` along `ll` ([lat, lon][]) over `duration` ms; onProgress(vertexIndex, [lat, lon], fraction).
 *  Resolves when done; the returned promise has .cancel(). */
export function animateRoute(line, ll, { duration = 2500, onProgress } = {}) {
  let raf = 0, cancelled = false, resolveFn;
  const p = new Promise((resolve) => { resolveFn = resolve; });
  const t0 = performance.now();
  const n = ll.length - 1;
  const frame = (now) => {
    if (cancelled) return;
    const f = Math.min(1, (now - t0) / duration), k = ease(f) * n, i = Math.floor(k), r = k - i;
    const a = ll[i], b = ll[Math.min(i + 1, n)];
    const tip = [a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r];
    line.setLatLngs(ll.slice(0, i + 1).concat([tip]));
    onProgress?.(i, tip, f);
    if (f < 1) raf = requestAnimationFrame(frame); else resolveFn(true);
  };
  raf = requestAnimationFrame(frame);
  p.cancel = () => { cancelled = true; cancelAnimationFrame(raf); resolveFn(false); };
  return p;
}
