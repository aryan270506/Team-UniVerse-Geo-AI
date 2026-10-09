// HazardMap dashboard: loads processed drives from the API and renders them on Leaflet.
const CAT = {
  pothole:         { label: "Pothole",         glyph: "P" },
  crack:           { label: "Crack",           glyph: "C" },
  alligator_crack: { label: "Alligator crack", glyph: "A" },
  flooding:        { label: "Flooding",        glyph: "W" },
  fallen_tree:     { label: "Fallen tree",     glyph: "T" },
  power_line:      { label: "Power line",      glyph: "E" },
  debris:          { label: "Debris",          glyph: "D" },
  landslide:       { label: "Landslide",       glyph: "L" },
  blockage:        { label: "Blockage",        glyph: "B" },
};
const SEV = { high: "#e5484d", medium: "#f5a524", low: "#46a758" };
const SEV_ORDER = { high: 0, medium: 1, low: 2 };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { runId: null, run: null, sev: new Set(Object.keys(SEV)), cats: new Set(Object.keys(CAT)), layers: {} };

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: false, preferCanvas: false }).setView([20.5, 78.9], 5);
L.control.zoom({ position: "topright" }).addTo(map);
const base = {
  Dark: L.layerGroup([
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 20, maxNativeZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap" }),
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 20, maxNativeZoom: 16 }),
  ]),
  Streets: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
  Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 20, maxNativeZoom: 19, attribution: "Imagery &copy; Esri" }),
};
base.Dark.addTo(map);
const trajLayer = L.layerGroup().addTo(map);
const hazardLayer = L.featureGroup().addTo(map);
L.control.layers(base, { "Vehicle trajectory": trajLayer, Hazards: hazardLayer }, { position: "topright" }).addTo(map);
L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);

$("#legend").innerHTML = Object.entries(SEV)
  .map(([k, c]) => `<span><i class="dot" style="background:${c}"></i>${k}</span>`).join("") +
  `<span><i class="dot" style="background:#4f8cff"></i>route</span>`;

function icon(p) {
  return L.divIcon({
    className: "", iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -14],
    html: `<div class="hz-icon ${p.severity}">${CAT[p.category]?.glyph ?? "?"}</div>`,
  });
}

function popupHtml(p) {
  const [lon, lat] = p.centroid;
  const img = p.snapshot ? `<img src="/runs/${encodeURIComponent(state.runId)}/${esc(p.snapshot)}" alt="${esc(p.category)} detection frame" loading="lazy">` : "";
  return `<div class="pop">${img}
    <h3>${esc(CAT[p.category]?.label ?? p.category)} <span class="badge ${p.severity}">${p.severity}</span></h3>
    <dl>
      <dt>ID</dt><dd>${esc(p.id)}</dd>
      <dt>Confidence</dt><dd>${(p.confidence * 100).toFixed(0)}% · ${p.frames} frames</dd>
      <dt>Severity score</dt><dd>${p.severity_score}</dd>
      <dt>Location</dt><dd>${lat.toFixed(6)}, ${lon.toFixed(6)}</dd>
      <dt>Range from car</dt><dd>${p.min_range_m} m${p.extent_m >= 15 ? ` · extent ${p.extent_m} m` : ""}</dd>
      <dt>Seen</dt><dd>${new Date(p.first_seen).toLocaleString()}</dd>
      <dt>Video time</dt><dd>${p.video_time_s}s</dd>
      <dt>GPS</dt><dd>${esc(p.gps_quality)} (gap ${p.gps_gap_s}s)</dd>
      <dt>Detector</dt><dd>${esc(p.labels.join(", "))}</dd>
    </dl>
    <div class="links">
      <a href="https://www.google.com/maps/search/?api=1&query=${lat},${lon}" target="_blank" rel="noopener">Google Maps</a>
      <a href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lon}" target="_blank" rel="noopener">Street View</a>
    </div></div>`;
}

// ---------------------------------------------------------------- render
function visible(f) {
  const p = f.properties;
  return state.sev.has(p.severity) && state.cats.has(p.category);
}

function render(fit = false) {
  hazardLayer.clearLayers();
  state.layers = {};
  const feats = (state.run?.hazards.features ?? []).filter(visible)
    .sort((a, b) => SEV_ORDER[a.properties.severity] - SEV_ORDER[b.properties.severity] ||
                    b.properties.severity_score - a.properties.severity_score);

  for (const f of feats) {
    const p = f.properties;
    const [lon, lat] = p.centroid;
    const group = L.featureGroup();
    if (f.geometry.type === "LineString") {
      L.polyline(f.geometry.coordinates.map(([x, y]) => [y, x]),
        { color: SEV[p.severity], weight: 7, opacity: .85, lineCap: "round" }).addTo(group);
    }
    L.marker([lat, lon], { icon: icon(p), riseOnHover: true, title: `${p.id} ${p.category}` }).addTo(group);
    group.bindPopup(popupHtml(p), { maxWidth: 320 });
    group.on("popupopen", () => highlight(p.id));
    group.addTo(hazardLayer);
    state.layers[p.id] = group;
  }

  $("#hazardList").innerHTML = feats.map((f) => {
    const p = f.properties;
    return `<li data-id="${esc(p.id)}" tabindex="0">
      <div class="hz-icon ${p.severity}" aria-hidden="true">${CAT[p.category]?.glyph ?? "?"}</div>
      <div><div class="t">${esc(CAT[p.category]?.label ?? p.category)}</div>
        <div class="s">${esc(p.id)} · ${(p.confidence * 100).toFixed(0)}% · ${p.frames}f · t=${p.video_time_s}s</div></div>
      <span class="badge ${p.severity}">${p.severity}</span></li>`;
  }).join("") || `<li class="muted">No hazards match the filters.</li>`;
  $("#listCount").textContent = `${feats.length} shown`;

  if (fit) fitRun();
}

function fitRun() {
  const b = L.featureGroup([...trajLayer.getLayers(), ...hazardLayer.getLayers()]).getBounds();
  if (b.isValid()) map.fitBounds(b, { padding: [40, 40], maxZoom: 18 });
}

function highlight(id) {
  document.querySelectorAll(".hazard-list li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  document.querySelector(`.hazard-list li[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
}

function focusHazard(id) {
  const g = state.layers[id];
  if (!g) return;
  map.flyTo(g.getBounds().getCenter(), Math.max(map.getZoom(), 18), { duration: .6 });
  map.once("moveend", () => g.openPopup());
}

$("#hazardList").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) focusHazard(li.dataset.id);
});
$("#hazardList").addEventListener("keydown", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); focusHazard(li.dataset.id); }
});

function renderSidebar() {
  const s = state.run.summary;
  const feats = state.run.hazards.features;
  const high = feats.filter((f) => f.properties.severity === "high").length;
  $("#kpis").innerHTML = [
    ["Hazards mapped", feats.length, ""],
    ["High severity", high, high ? "alert" : ""],
    ["Route covered", `${s.route_km} km`, ""],
    ["Inference speed", `${s.inference_fps} fps`, ""],
  ].map(([k, v, c]) => `<div class="kpi ${c}"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");

  const sevCount = (k) => feats.filter((f) => f.properties.severity === k).length;
  $("#sevFilters").innerHTML = Object.entries(SEV).map(([k, c]) => `
    <label class="chip"><input type="checkbox" value="${k}" ${state.sev.has(k) ? "checked" : ""}>
      <i class="dot" style="background:${c}"></i>${k} ${sevCount(k)}</label>`).join("");

  const counts = s.by_category || {};
  $("#catFilters").innerHTML = Object.entries(CAT).map(([k, c]) => `
    <label class="cat"><input type="checkbox" value="${k}" ${state.cats.has(k) ? "checked" : ""} ${counts[k] ? "" : "disabled"}>
      ${c.label}<span class="n">${counts[k] || 0}</span></label>`).join("");

  $("#runMeta").innerHTML = `
    <b>${esc(s.video)}</b> + <b>${esc(s.gps)}</b><br>
    Start ${new Date(s.video_start).toLocaleString()} · ${s.duration_s}s · ${s.resolution.join("×")}<br>
    Sync: ${esc(s.sync_method)}<br>
    ${s.frames_processed} frames · ${s.raw_detections} raw → ${s.geolocated_detections} geolocated → ${s.hazards} hazards<br>
    Processed in ${s.wall_time_s}s on ${esc(s.device)}`;
}

$("#sevFilters").addEventListener("change", (e) => {
  e.target.checked ? state.sev.add(e.target.value) : state.sev.delete(e.target.value);
  render();
});
$("#catFilters").addEventListener("change", (e) => {
  e.target.checked ? state.cats.add(e.target.value) : state.cats.delete(e.target.value);
  render();
});
$("#toggleCats").addEventListener("click", () => {
  const all = state.cats.size > 0;
  state.cats = all ? new Set() : new Set(Object.keys(CAT));
  $("#toggleCats").textContent = all ? "all" : "none";
  renderSidebar();
  render();
});

// ---------------------------------------------------------------- data
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function loadRuns(selectId) {
  const runs = await api("/api/runs");
  $("#emptyState").hidden = runs.length > 0;
  $("#runSelect").innerHTML = runs.map((r) =>
    `<option value="${esc(r.id)}">${esc(r.id)} · ${r.hazards} hazards</option>`).join("");
  const id = selectId || new URLSearchParams(location.search).get("run") || runs[0]?.id;
  if (id) { $("#runSelect").value = id; await loadRun(id); }
}

async function loadRun(id) {
  state.runId = id;
  state.run = await api(`/api/runs/${encodeURIComponent(id)}`);
  history.replaceState(null, "", `?run=${encodeURIComponent(id)}`);
  trajLayer.clearLayers();
  for (const f of state.run.trajectory.features) {
    const ll = f.geometry.coordinates.map(([x, y]) => [y, x]);
    L.polyline(ll, { color: "#000", weight: 7, opacity: .35 }).addTo(trajLayer);
    L.polyline(ll, { color: "#4f8cff", weight: 4, opacity: .9 }).addTo(trajLayer);
    L.circleMarker(ll[0], { radius: 6, color: "#fff", weight: 2, fillColor: "#46a758", fillOpacity: 1 })
      .bindTooltip("Start").addTo(trajLayer);
    L.circleMarker(ll.at(-1), { radius: 6, color: "#fff", weight: 2, fillColor: "#e5484d", fillOpacity: 1 })
      .bindTooltip("End").addTo(trajLayer);
  }
  renderSidebar();
  render(true);
}

$("#runSelect").addEventListener("change", (e) => loadRun(e.target.value));

// export menu
$("#exportBtn").addEventListener("click", (e) => { e.stopPropagation(); $("#exportMenu").classList.toggle("open"); });
document.addEventListener("click", () => $("#exportMenu").classList.remove("open"));
$("#exportMenu").addEventListener("click", (e) => {
  const f = e.target.dataset.file;
  if (f && state.runId) location.href = `/api/runs/${encodeURIComponent(state.runId)}/download/${f}`;
});

// ---------------------------------------------------------------- upload
const dlg = $("#uploadDlg");
function openUpload() {
  $("#uploadError").hidden = true;
  $("#progress").hidden = true;
  $("#submitUpload").disabled = false;
  dlg.showModal();
}
window.openUpload = openUpload;
$("#newRunBtn").addEventListener("click", openUpload);
$("#cancelUpload").addEventListener("click", () => dlg.close());

$("#uploadForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  fd.set("use_world", e.target.use_world.checked ? "true" : "false");
  $("#submitUpload").disabled = true;
  $("#progress").hidden = false;
  $("#uploadError").hidden = true;
  setProgress(0, "Uploading…");

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/process");
  xhr.upload.onprogress = (ev) => ev.lengthComputable && setProgress(ev.loaded / ev.total * .2, `Uploading… ${(ev.loaded / 1e6).toFixed(0)} MB`);
  xhr.onload = () => {
    if (xhr.status >= 300) return fail(JSON.parse(xhr.responseText).detail || xhr.statusText);
    poll(JSON.parse(xhr.responseText).id);
  };
  xhr.onerror = () => fail("Upload failed");
  xhr.send(fd);
});

function setProgress(frac, text) {
  $("#progressBar").style.width = `${Math.round(frac * 100)}%`;
  $("#progressText").textContent = text;
}
function fail(msg) {
  $("#uploadError").textContent = msg;
  $("#uploadError").hidden = false;
  $("#submitUpload").disabled = false;
}
async function poll(jobId) {
  const job = await api(`/api/jobs/${jobId}`);
  if (job.state === "error") return fail(job.error);
  if (job.state === "done") {
    setProgress(1, "Done");
    dlg.close();
    return loadRuns(job.run_id);
  }
  const frac = job.stage === "detecting" ? .2 + job.progress * .75 : job.stage === "geolocating" ? .97 : .2;
  setProgress(frac, `${job.stage[0].toUpperCase() + job.stage.slice(1)}… ${job.stage === "detecting" ? Math.round(job.progress * 100) + "%" : ""}`);
  setTimeout(() => poll(jobId), 1000);
}

loadRuns().catch((e) => console.error(e));
