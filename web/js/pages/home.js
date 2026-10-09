// Home: the tracking view. Left: new drive · selected case "tracking card" · case list.
// Right: map with every open case, the selected case's drive route and a floating case card.
import { $, $$, api, avatar, caseHref, catTile, coord, esc, prioTag, short, sla, statusTag, store, userRuns } from "../core.js";
import { addCase, drawRoute, makeMap, pinIcon, renderClosures } from "../map.js";
import { openLive, openUpload } from "../dialogs.js";

const PRIO_RANK = { P1: 0, P2: 1, P3: 2 };

export default async function render(el, _params, query) {
  const { cases } = await store.cases(true);
  const runs = userRuns(await api("/api/runs").catch(() => []));
  const sorted = cases.slice().sort((a, b) => (a.status === "resolved") - (b.status === "resolved")
    || PRIO_RANK[a.priority] - PRIO_RANK[b.priority] || b.first_seen.localeCompare(a.first_seen));
  let filter = "open";
  let selected = sorted.find((c) => c.id === query.get("case")) || sorted.find((c) => c.status !== "resolved") || sorted[0] || null;

  el.innerHTML = `
    <div class="home">
      <div class="home-left">
        <section class="new-card" aria-labelledby="newTitle">
          <span class="glass" aria-hidden="true"></span>
          <h2 id="newTitle">Process a new drive</h2>
          <p>Upload dashcam video + GPS log to map road hazards</p>
          <div class="pill-input" id="newPill" role="button" tabindex="0" aria-label="Upload a drive">
            <span>Video (MP4) + GPS track (GPX/CSV)</span>
            <button class="btn round" id="newGo" aria-label="Upload a drive"><i data-lucide="arrow-right"></i></button>
          </div>
          <p class="alt">or <button id="liveGo">go live with a phone camera →</button></p>
        </section>

        <section class="track-card" id="trackCard" aria-live="polite"></section>

        <section class="list-card" aria-label="Cases">
          <div class="list-head">
            <h2 style="font-size:var(--fs-lg)">Cases</h2>
            <div class="tabs" id="listTabs" role="tablist">
              <button data-f="open" class="on">Open</button><button data-f="p1">Critical</button><button data-f="all">All</button>
            </div>
          </div>
          <ul class="case-list" id="caseList"></ul>
        </section>
      </div>

      <section class="map-card" aria-label="Map">
        <div class="map" id="homeMap"></div>
        <div class="float-card" id="floatCard" hidden></div>
      </section>
    </div>`;

  const map = makeMap($("#homeMap", el));
  const allLayer = L.featureGroup().addTo(map), routeLayer = L.layerGroup().addTo(map), closLayer = L.layerGroup().addTo(map);
  const selLayer = L.layerGroup().addTo(map);
  const trajCache = {}, closCache = {};
  let alive = true;

  const listed = () => sorted.filter((c) => filter === "all" || (c.status !== "resolved" && (filter === "open" || c.priority === "P1")));

  function drawList() {
    const items = listed();
    $("#caseList", el).innerHTML = items.map((c) => `
      <li class="ci ${selected?.id === c.id ? "on" : ""}" data-id="${esc(c.id)}" tabindex="0">
        ${catTile(c.category)}
        <div style="min-width:0"><div class="t">${esc(c.number)}</div>
          <div class="m">${esc(c.label)} <i data-lucide="arrow-right"></i> ${esc(c.department)}</div></div>
        ${statusTag(c.status)}</li>`).join("") || `<li class="tc-empty">No cases here yet.</li>`;
  }

  function drawCard() {
    const c = selected;
    if (!c) {
      $("#trackCard", el).innerHTML = `<div class="tc-empty">Process a drive to start tracking hazards.</div>`;
      $("#floatCard", el).hidden = true;
      return;
    }
    const s = sla(c);
    const owner = c.assignee || c.department;
    $("#trackCard", el).innerHTML = `
      <div class="tc-row">
        <div><span class="tc-cap">Case number</span><b>${esc(c.number)}</b></div>
        <div style="display:flex;gap:6px">${prioTag(c.priority)}${statusTag(c.status)}</div>
      </div>
      <hr class="divider">
      <div class="tc-time">
        <div><span class="tc-cap">Detected</span><b>${short(new Date(c.first_seen).getTime())}</b></div>
        <div class="tc-track" title="${s.done ? "Resolved" : s.overdue ? "Past target" : `${Math.round(s.frac * 100)}% of target time used`}">
          <span class="pin-mini" style="left:${(s.done ? 1 : s.frac) * 100}%"></span></div>
        <div class="right"><span class="tc-cap">${s.done ? "Resolved" : "Target fix"}</span><b>${short(s.done ? new Date(c.resolved_at).getTime() : s.end)}</b>
          ${s.overdue ? `<span class="chip warn" style="margin-top:4px"><i></i>Overdue</span>` : ""}</div>
      </div>
      <hr class="divider">
      <div class="tc-facts">
        <div><span class="tc-cap">Type</span><b>${esc(c.label)}</b></div>
        <div><span class="tc-cap">Confidence</span><b>${Math.round(c.confidence * 100)}%</b></div>
        <div><span class="tc-cap">${c.extent_m >= 15 ? "Extent" : "Frames"}</span><b>${c.extent_m >= 15 ? `${c.extent_m} m` : c.frames}</b></div>
        <div><span class="tc-cap">Drive</span><b title="${esc(c.run_id)}">${esc(c.run_id)}</b></div>
      </div>
      <hr class="divider">
      <div class="tc-resp">
        ${avatar(owner, "lg")}
        <div class="who"><span class="tc-cap">Responsible</span><b>${esc(owner)}</b></div>
        <a class="btn round" href="https://www.google.com/maps/search/?api=1&query=${c.centroid[1]},${c.centroid[0]}" target="_blank" rel="noopener" aria-label="Open location in Google Maps"><i data-lucide="map-pin"></i></a>
        <a class="btn round" href="${caseHref(c)}" aria-label="Open case"><i data-lucide="message-square"></i></a>
      </div>`;
    const f = $("#floatCard", el);
    f.innerHTML = `<b>${esc(c.number)} · ${esc(c.label)}</b>${statusTag(c.status)}`;
    f.hidden = false;
  }

  async function drawMap(fly) {
    allLayer.clearLayers();
    sorted.filter((c) => c.status !== "resolved" && c.id !== selected?.id).forEach((c) => {
      const g = addCase(allLayer, c);
      g.on("click", () => select(c.id, false));
    });
    selLayer.clearLayers(); routeLayer.clearLayers(); closLayer.clearLayers();
    if (!selected) {
      const b = allLayer.getBounds();
      if (b.isValid()) map.fitBounds(b, { padding: [60, 60], maxZoom: 15 });
      return;
    }
    const c = selected, run = c.run_id;
    if (!(run in trajCache)) trajCache[run] = await api(`/api/runs/${encodeURIComponent(run)}`).then((d) => d.trajectory.features[0]?.geometry.coordinates || []).catch(() => []);
    if (!alive || selected !== c) return;
    drawRoute(routeLayer, trajCache[run]);
    if (c.geometry?.type === "LineString") L.polyline(c.geometry.coordinates.map(([x, y]) => [y, x]), { color: "#ef4444", weight: 8, opacity: .75, lineCap: "round" }).addTo(selLayer);
    L.marker([c.centroid[1], c.centroid[0]], { icon: pinIcon(), zIndexOffset: 1000, title: c.number }).addTo(selLayer);
    if (fly) {
      const pts = trajCache[run].map(([x, y]) => [y, x]);
      const b = pts.length > 1 ? L.latLngBounds(pts).extend([c.centroid[1], c.centroid[0]]) : null;
      if (b?.isValid()) map.flyToBounds(b, { padding: [90, 90], maxZoom: 17, duration: .6 });
      else map.flyTo([c.centroid[1], c.centroid[0]], 16, { duration: .6 });
    }
    if (["flooding", "landslide", "fallen_tree", "power_line", "blockage", "debris"].includes(c.category) && c.priority !== "P3") {
      if (!(run in closCache)) closCache[run] = await api(`/api/runs/${encodeURIComponent(run)}/closures`).then((d) => d.closures).catch(() => []);
      if (alive && selected === c) renderClosures(closLayer, closCache[run].filter((x) => x.hazard_id === c.hazard_id));
    }
  }

  function select(id, fly = true) {
    selected = sorted.find((c) => c.id === id) || selected;
    history.replaceState(null, "", `#/home?case=${encodeURIComponent(selected.id)}`);
    drawList(); drawCard(); drawMap(fly);
  }

  drawList(); drawCard(); drawMap(true);
  if (!selected && !runs.length) $("#trackCard", el).innerHTML = `<div class="tc-empty">No drives yet. Upload one to begin.</div>`;

  $("#caseList", el).addEventListener("click", (e) => { const li = e.target.closest(".ci[data-id]"); if (li) select(li.dataset.id); });
  $("#caseList", el).addEventListener("keydown", (e) => { const li = e.target.closest(".ci[data-id]"); if (li && e.key === "Enter") select(li.dataset.id); });
  $("#listTabs", el).addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    filter = b.dataset.f;
    $$("#listTabs button", el).forEach((x) => x.classList.toggle("on", x === b));
    drawList();
  });
  $("#newPill", el).addEventListener("click", (e) => { e.preventDefault(); openUpload(); });
  $("#newPill", el).addEventListener("keydown", (e) => { if (e.key === "Enter") openUpload(); });
  $("#liveGo", el).addEventListener("click", () => openLive());
  return () => { alive = false; map.remove(); };
}
