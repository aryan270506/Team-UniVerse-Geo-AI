import { $, api, date, esc, healthOf, pageHead } from "../core.js";
import { userRuns } from "../core.js";
import { openLive, openUpload } from "../dialogs.js";

export default async function render(el) {
  const [all, o] = await Promise.all([api("/api/runs"), api("/api/overview")]);
  const runs = userRuns(all);
  const health = Object.fromEntries(o.drives.map((d) => [d.id, d.health]));
  const modeLabel = { batch: "Processed", replay: "Live replay", device: "Phone live" };
  const SRC = { upload: "Uploaded", record: "In-app recording", live: "Live from phone" };

  el.innerHTML = `
    ${pageHead("05", "Drives", "Survey drives", `<button class="btn accent" id="dLive"><span class="rec"></span>Go live</button><button class="btn primary" id="dNew"><i data-lucide="plus"></i>New drive</button>`)}
    <div class="table-wrap cards"><table class="table">
      <thead><tr><th>Drive</th><th>Contributor</th><th>Recorded</th><th>Mode</th><th>Length</th><th>Route</th><th>Hazards</th><th>Health</th><th></th></tr></thead>
      <tbody>${runs.map((r) => {
        const h = health[r.id] || healthOf([], r.route_km);
        const tone = h.score >= 70 ? "" : h.score >= 55 ? "mid" : "bad";
        const cats = Object.entries(r.by_category || {}).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ");
        return `<tr data-href="#/command/${encodeURIComponent(r.id)}">
          <td><div class="id">${esc(r.id)}</div><div class="muted" style="font-size:12px">${esc(r.video)}</div></td>
          <td>${r.contributor ? `<a href="#/users/${r.contributor.user_id}">${esc(r.contributor.user)}</a><div class="muted" style="font-size:12px">${esc(SRC[r.contributor.source] || "")}</div>` : `<span class="muted">Control room</span>`}</td>
          <td>${date(r.video_start)}</td>
          <td><span class="label">${esc(modeLabel[r.mode] || "Processed")}</span></td>
          <td class="num" style="font-weight:700">${Math.round(r.duration_s)}s</td>
          <td class="num" style="font-weight:700">${r.route_km} km</td>
          <td><b class="num">${r.hazards}</b> <span class="muted" style="font-size:12px">${esc(cats)}</span></td>
          <td><span class="grade-tag ${tone}" title="${h.score}/100">${h.grade}</span></td>
          <td style="white-space:nowrap">
            <a class="btn sm" href="#/command/${encodeURIComponent(r.id)}" title="Open in Command"><i data-lucide="radar"></i></a>
            <a class="btn sm" href="/report.html?run=${encodeURIComponent(r.id)}" target="_blank" rel="noopener" title="Incident report"><i data-lucide="file-text"></i></a>
            <a class="btn sm" href="/api/runs/${encodeURIComponent(r.id)}/download/hazards.geojson" title="Download GeoJSON"><i data-lucide="download"></i></a>
          </td></tr>`;
      }).join("") || `<tr class="empty-row"><td colspan="9">No drives yet. Upload one with New drive.</td></tr>`}</tbody>
    </table></div>`;

  $("#dNew", el).addEventListener("click", openUpload);
  $("#dLive", el).addEventListener("click", () => openLive());
  el.querySelector("tbody").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const tr = e.target.closest("tr[data-href]");
    if (tr) location.hash = tr.dataset.href;
  });
}
