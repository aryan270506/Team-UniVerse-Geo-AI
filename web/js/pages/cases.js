import { $, ago, caseHref, coord, esc, glyph, icons, pageHead, prioTag, statusTag, store, wxIcon, CAT, STATUSES, STATUS_LABEL } from "../core.js";

export default async function render(el, _params, query) {
  const { cases, departments } = await store.cases(true);
  const f = { status: query.get("status") || "open", prio: query.get("prio") || "", type: "", dept: "", auth: "", q: "" };
  const auths = [...new Set(cases.map((c) => c.authority_code).filter(Boolean))].sort();

  const types = [...new Set(cases.map((c) => c.category))];
  el.innerHTML = `
    ${pageHead("02", "Cases", "Cases", `<button class="btn" id="exportCsv"><i data-lucide="download"></i>Export CSV</button><a class="btn primary" href="#/board"><i data-lucide="columns-3"></i>Board view</a>`)}
    <div class="filters">
      <div class="tabs" role="tablist" id="statusTabs"></div>
      <label class="field"><span>Priority</span><select class="select" id="fPrio"><option value="">All</option><option>P1</option><option>P2</option><option>P3</option></select></label>
      <label class="field"><span>Type</span><select class="select" id="fType"><option value="">All</option>${types.map((t) => `<option value="${t}">${esc(CAT[t]?.label ?? t)}</option>`).join("")}</select></label>
      <label class="field"><span>Department</span><select class="select" id="fDept"><option value="">All</option>${departments.map((d) => `<option>${esc(d)}</option>`).join("")}</select></label>
      <label class="field"><span>Authority</span><select class="select" id="fAuth"><option value="">All</option>${auths.map((a) => `<option>${esc(a)}</option>`).join("")}<option value="-">None nearby</option></select></label>
      <label class="field search"><span>Search</span><input class="input line" id="fQ" placeholder="ID, type, drive, authority…" type="search"></label>
    </div>
    <div class="table-wrap cards"><table class="table">
      <thead><tr><th>Case</th><th>Type</th><th>Pri</th><th>Status</th><th>Department</th><th>Authority</th><th>Weather</th><th>Drive</th><th>Contributor</th><th>Location</th><th>Detected</th></tr></thead>
      <tbody id="rows"></tbody>
    </table></div>
    <p class="label" id="count" style="margin-top:12px"></p>`;

  $("#fPrio", el).value = f.prio;
  const match = (c, ignoreStatus) =>
    (ignoreStatus || (f.status === "open" ? c.status !== "resolved" : f.status === "all" || c.status === f.status)) &&
    (!f.prio || c.priority === f.prio) && (!f.type || c.category === f.type) && (!f.dept || c.department === f.dept) &&
    (!f.auth || (f.auth === "-" ? !c.authority_code : c.authority_code === f.auth)) &&
    (!f.q || `${c.number} ${c.label} ${c.department} ${c.authority_code || ""} ${c.authority?.city || ""} ${c.contributor?.user || ""} ${c.run_id} ${c.drive.video}`.toLowerCase().includes(f.q));

  const draw = () => {
    const base = cases.filter((c) => match(c, true));
    const tabs = [["open", "Open"], ...STATUSES.map((s) => [s, STATUS_LABEL[s]]), ["all", "All"]];
    $("#statusTabs", el).innerHTML = tabs.map(([k, l]) => {
      const n = k === "all" ? base.length : k === "open" ? base.filter((c) => c.status !== "resolved").length : base.filter((c) => c.status === k).length;
      return `<button class="${f.status === k ? "on" : ""}" data-s="${k}" role="tab" aria-selected="${f.status === k}">${l}<span class="c">${n}</span></button>`;
    }).join("");
    const rows = cases.filter((c) => match(c)).sort((a, b) => a.priority.localeCompare(b.priority) || b.first_seen.localeCompare(a.first_seen));
    $("#rows", el).innerHTML = rows.map((c) => `<tr data-href="${caseHref(c)}" tabindex="0">
      <td class="id">${esc(c.number)}</td><td><span class="kind">${glyph(c)}${esc(c.label)}</span></td>
      <td>${prioTag(c.priority)}</td><td>${statusTag(c.status)}</td><td>${esc(c.department)}</td>
      <td style="white-space:nowrap" title="${esc(c.authority ? `${c.authority.designation}, ${c.authority.city}` : "")}">${c.authority ? esc(c.authority.office_code) : `<span class="muted">—</span>`}</td>
      <td style="white-space:nowrap" title="${esc(c.weather ? [c.weather.label, ...c.weather.flags].join(" · ") : "")}">${c.weather
        ? `<span class="kind">${wxIcon(c.weather)}${Math.round(c.weather.temp_c)}°${c.weather.rain_24h ? ` · ${c.weather.rain_24h} mm` : ""}</span>${c.weather.flags.length ? ` <span class="chip warn"><i></i>${c.weather.flags.length}</span>` : ""}`
        : `<span class="muted">—</span>`}</td>
      <td class="muted">${esc(c.run_id)}</td><td>${c.contributor ? esc(c.contributor.user) : `<span class="muted">Control room</span>`}</td><td class="coord">${coord(c.centroid)}</td><td class="muted">${ago(c.first_seen)}</td></tr>`).join("")
      || `<tr class="empty-row"><td colspan="11">No cases match these filters.</td></tr>`;
    icons(el);
    $("#count", el).textContent = `${rows.length} of ${cases.length} cases`;
    el._rows = rows;
  };
  draw();

  $("#statusTabs", el).addEventListener("click", (e) => { const b = e.target.closest("[data-s]"); if (b) { f.status = b.dataset.s; draw(); } });
  $("#fPrio", el).addEventListener("change", (e) => { f.prio = e.target.value; draw(); });
  $("#fType", el).addEventListener("change", (e) => { f.type = e.target.value; draw(); });
  $("#fDept", el).addEventListener("change", (e) => { f.dept = e.target.value; draw(); });
  $("#fAuth", el).addEventListener("change", (e) => { f.auth = e.target.value; draw(); });
  $("#fQ", el).addEventListener("input", (e) => { f.q = e.target.value.trim().toLowerCase(); draw(); });
  const go = (tr) => tr && (location.hash = tr.dataset.href);
  $("#rows", el).addEventListener("click", (e) => go(e.target.closest("tr[data-href]")));
  $("#rows", el).addEventListener("keydown", (e) => { if (e.key === "Enter") go(e.target.closest("tr[data-href]")); });
  $("#exportCsv", el).addEventListener("click", () => {
    const head = ["case", "type", "priority", "status", "department", "authority", "authority_designation", "authority_phone",
      "authority_email", "authority_address", "escalation", "escalation_phone",
      "weather", "temp_c", "rain_24h_mm", "rain_72h_mm", "weather_flags", "drive", "contributor", "lat", "lon", "detected", "confidence"];
    const au = (c) => c.authority || {}, es = (c) => c.escalation || {};
    const lines = (el._rows || []).map((c) => [c.number, c.label, c.priority, c.status, c.department,
      au(c).office_code || "", au(c).designation || "", (au(c).phones || []).join(" / "), (au(c).emails || []).join(" / "), au(c).address || "",
      es(c).office_code || "", (es(c).phones || []).join(" / "),
      c.weather?.label || "", c.weather?.temp_c ?? "", c.weather?.rain_24h ?? "", c.weather?.rain_72h ?? "", (c.weather?.flags || []).join(" / "), c.run_id, c.contributor?.user || "", c.centroid[1], c.centroid[0], c.first_seen, c.confidence]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" }));
    a.download = "hazard-cases.csv";
    a.click();
  });
}
