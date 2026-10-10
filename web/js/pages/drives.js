import { $, api, date, esc, healthOf, pageHead, store, toast } from "../core.js";
import { userRuns } from "../core.js";
import { openLive, openUpload } from "../dialogs.js";

export default async function render(el) {
  const [all, o] = await Promise.all([api("/api/runs"), api("/api/overview")]);
  const runs = userRuns(all);
  const health = Object.fromEntries(o.drives.map((d) => [d.id, d.health]));
  const modeLabel = { batch: "Processed", replay: "Live replay", device: "Phone live" };
  const SRC = { upload: "Uploaded", record: "In-app recording", live: "Live from phone" };

  el.innerHTML = `
    ${pageHead("05", "Drives", "Survey drives", `<button class="btn danger" id="dDel" hidden><i data-lucide="trash-2"></i><span>Delete</span></button><button class="btn accent" id="dLive"><span class="rec"></span>Go live</button><button class="btn primary" id="dNew"><i data-lucide="plus"></i>New drive</button>`)}
    <div class="table-wrap cards"><table class="table">
      <thead><tr><th class="pick"><input type="checkbox" id="dAll" aria-label="Select all drives"></th><th>Drive</th><th>Contributor</th><th>Recorded</th><th>Mode</th><th>Length</th><th>Route</th><th>Hazards</th><th>Health</th><th></th></tr></thead>
      <tbody>${runs.map((r) => {
        const h = health[r.id] || healthOf([], r.route_km);
        const tone = h.score >= 70 ? "" : h.score >= 55 ? "mid" : "bad";
        const cats = Object.entries(r.by_category || {}).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ");
        return `<tr data-href="#/command/${encodeURIComponent(r.id)}" data-id="${esc(r.id)}">
          <td class="pick"><input type="checkbox" class="dPick" value="${esc(r.id)}" aria-label="Select ${esc(r.id)}"></td>
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
            <button class="btn sm dDelOne" data-id="${esc(r.id)}" title="Delete drive"><i data-lucide="trash-2"></i></button>
          </td></tr>`;
      }).join("") || `<tr class="empty-row"><td colspan="10">No drives yet. Upload one with New drive.</td></tr>`}</tbody>
    </table></div>`;

  $("#dNew", el).addEventListener("click", openUpload);
  $("#dLive", el).addEventListener("click", () => openLive());
  const picked = () => [...el.querySelectorAll(".dPick:checked")].map((b) => b.value);
  const syncPick = () => {
    const n = picked().length, boxes = el.querySelectorAll(".dPick");
    $("#dDel", el).hidden = !n;
    $("#dDel span", el).textContent = `Delete ${n} drive${n === 1 ? "" : "s"}`;
    $("#dAll", el).checked = n > 0 && n === boxes.length;
    $("#dAll", el).indeterminate = n > 0 && n < boxes.length;
  };
  async function remove(ids) {
    const what = ids.length === 1 ? `drive "${ids[0]}"` : `${ids.length} drives`;
    if (!confirm(`Delete ${what}? Its video, hazards, cases and their history are removed for good, and any coins it earned are taken back.`)) return;
    let ok = 0;
    const failed = [];
    for (const id of ids) {
      try { await api(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }); ok++; }
      catch (err) { failed.push(`${id}: ${err.message}`); }
    }
    store.invalidate();
    if (ok) toast(`Deleted ${ok} drive${ok === 1 ? "" : "s"}`);
    if (failed.length) toast(`Not deleted: ${esc(failed.join("; "))}`);
    await render(el);
  }
  $("#dAll", el).addEventListener("change", (e) => {
    el.querySelectorAll(".dPick").forEach((b) => { b.checked = e.target.checked; });
    syncPick();
  });
  $("#dDel", el).addEventListener("click", () => remove(picked()));
  el.querySelector("tbody").addEventListener("change", (e) => { if (e.target.matches(".dPick")) syncPick(); });
  el.querySelector("tbody").addEventListener("click", (e) => {
    const del = e.target.closest(".dDelOne");
    if (del) return remove([del.dataset.id]);
    if (e.target.closest("a, .pick")) return;
    const tr = e.target.closest("tr[data-href]");
    if (tr) location.hash = tr.dataset.href;
  });
}
