// Drives list (#/drives) and one drive with its hazards, map and coins (#/drives/<id>).
import { api, CAT, esc, wxIcon } from "/js/core.js";
import { drawRoute, makeMap, SEV_COL } from "/js/map.js";
import { driveRow, driveStatus, fmtDur, refreshBalance, SRC_LABEL } from "/app/app.js";

const SEV_PRIO = { high: "P1", medium: "P2", low: "P3" };

export default async function render(el, [id]) {
  return id ? detail(el, id) : list(el);
}

async function list(el) {
  const { drives } = await api("/api/me/drives");
  el.innerHTML = `
    <h1 class="u-h1">Your drives</h1>
    <div class="list">${drives.map(driveRow).join("") || `<div class="card empty">No drives yet.<br><a class="btn primary" style="margin-top:14px" href="#/record">Record your first drive</a></div>`}</div>`;
  if (drives.some((d) => d.status === "queued" || d.status === "processing")) return poll();
}

const poll = () => {
  const t = setTimeout(() => window.dispatchEvent(new HashChangeEvent("hashchange")), 6000);
  return () => clearTimeout(t);
};

async function detail(el, id) {
  const d = await api(`/api/me/drives/${encodeURIComponent(id)}`);
  const busy = d.status === "queued" || d.status === "processing";
  const credited = d.ledger.filter((x) => x.status === "credited");
  el.innerHTML = `
    <div><a class="label" href="#/drives">← Drives</a><h1 class="u-h1">${esc(d.name)}</h1></div>
    <section class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <div>${driveStatus(d)} <span class="label" style="margin-left:6px">${SRC_LABEL[d.source]} · ${new Date(d.created_at).toLocaleString()}</span></div>
        <div style="display:flex;align-items:center;gap:8px;font-weight:600;font-size:var(--fs-xl)"><span class="coin">T</span>${d.coins || (d.coins_pending ? `≈${d.coins_pending}` : 0)}</div>
      </div>
      ${busy ? `<p class="label" style="margin:12px 0 0">${d.status === "queued" ? `Waiting for the analyser${d.queue ? ` (#${d.queue} in line)` : ""}.` : "The AI is analysing your footage."} This page updates by itself.</p>` : ""}
      ${d.status === "failed" ? `<p class="err-box" style="margin-top:12px">${esc(d.error || "Analysis failed")}</p>` : ""}
      ${d.note ? `<p class="label" style="margin:12px 0 0">${esc(d.note)}</p>` : ""}
      ${!busy && d.status !== "failed" ? `<dl class="facts" style="margin-top:12px">
        <dt>Duration</dt><dd>${fmtDur(d.duration_s)}${d.valid_s != null && d.valid_s !== d.duration_s ? ` · ${fmtDur(d.valid_s)} valid` : ""}</dd>
        <dt>Distance</dt><dd>${d.route_km ?? "–"} km</dd>
        <dt>Hazards</dt><dd>${d.hazards ?? 0}</dd>
        <dt>Coins</dt><dd>${credited.map((x) => `${x.amount > 0 ? "+" : ""}${x.amount} ${esc(x.note || x.kind)}`).join("<br>") || "0"}</dd>
      </dl>` : ""}
    </section>
    ${d.route.length > 1 ? `<div class="u-map" id="map"></div>` : ""}
    ${d.hazards_list.length ? `<section><div class="section-head"><h2>Hazards found</h2><span class="label">${d.hazards_list.length}</span></div>
      <div class="list">${d.hazards_list.map((h) => `<div class="hz">
        ${h.snapshot ? `<img src="${esc(h.snapshot)}" alt="${esc(CAT[h.category]?.label || h.category)}" loading="lazy">` : "<span></span>"}
        <div><b>${esc(CAT[h.category]?.label || h.category)}</b> <span class="chip prio-${SEV_PRIO[h.severity]}"><i></i>${esc(h.severity)}</span>
          <div class="m">${Math.round(h.confidence * 100)}% sure · ${new Date(h.first_seen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
          ${h.weather ? `<div class="m">${wxIcon(h.weather)} ${esc(h.weather.label)}, ${Math.round(h.weather.temp_c)}°C</div>` : ""}</div></div>`).join("")}</div></section>`
      : !busy && d.status !== "failed" ? `<div class="card empty">No hazards on this drive. You still earn coins for the footage.</div>` : ""}`;

  if (d.route.length > 1) {
    const map = makeMap(document.getElementById("map"), { layers: false });
    const g = L.featureGroup().addTo(map);
    drawRoute(g, d.route);
    for (const h of d.hazards_list) {
      const [lon, lat] = h.centroid;
      L.circleMarker([lat, lon], { radius: 8, color: "#fff", weight: 2, fillColor: SEV_COL[SEV_PRIO[h.severity]], fillOpacity: 1 })
        .bindTooltip(CAT[h.category]?.label || h.category).addTo(g);
    }
    map.fitBounds(g.getBounds(), { padding: [24, 24], maxZoom: 17 });
  }
  if (busy) return poll();
  refreshBalance();
}
