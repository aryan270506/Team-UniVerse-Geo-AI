import { $, ago, caseHref, coord, esc, glyph, pageHead, prioTag, statusTag, store, CAT, STATUSES, STATUS_LABEL } from "../core.js";

export default async function render(el, _params, query) {
  const { cases, departments } = await store.cases(true);
  const f = { status: query.get("status") || "open", prio: query.get("prio") || "", type: "", dept: "", q: "" };

  const types = [...new Set(cases.map((c) => c.category))];
  el.innerHTML = `
    ${pageHead("02", "Cases", "Cases", `<button class="btn" id="exportCsv"><i data-lucide="download"></i>Export CSV</button><a class="btn primary" href="#/board"><i data-lucide="columns-3"></i>Board view</a>`)}
    <div class="filters">
      <div class="tabs" role="tablist" id="statusTabs"></div>
      <label class="field"><span>Priority</span><select class="select" id="fPrio"><option value="">All</option><option>P1</option><option>P2</option><option>P3</option></select></label>
      <label class="field"><span>Type</span><select class="select" id="fType"><option value="">All</option>${types.map((t) => `<option value="${t}">${esc(CAT[t]?.label ?? t)}</option>`).join("")}</select></label>
      <label class="field"><span>Department</span><select class="select" id="fDept"><option value="">All</option>${departments.map((d) => `<option>${esc(d)}</option>`).join("")}</select></label>
      <label class="field search"><span>Search</span><input class="input line" id="fQ" placeholder="ID, type, drive…" type="search"></label>
    </div>
    <div class="table-wrap cards"><table class="table">
      <thead><tr><th>Case</th><th>Type</th><th>Pri</th><th>Status</th><th>Department</th><th>Drive</th><th>Location</th><th>Detected</th></tr></thead>
      <tbody id="rows"></tbody>
    </table></div>
    <p class="label" id="count" style="margin-top:12px"></p>`;

  $("#fPrio", el).value = f.prio;
  const match = (c, ignoreStatus) =>
    (ignoreStatus || (f.status === "open" ? c.status !== "resolved" : f.status === "all" || c.status === f.status)) &&
    (!f.prio || c.priority === f.prio) && (!f.type || c.category === f.type) && (!f.dept || c.department === f.dept) &&
    (!f.q || `${c.number} ${c.label} ${c.department} ${c.run_id} ${c.drive.video}`.toLowerCase().includes(f.q));

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
      <td class="muted">${esc(c.run_id)}</td><td class="coord">${coord(c.centroid)}</td><td class="muted">${ago(c.first_seen)}</td></tr>`).join("")
      || `<tr class="empty-row"><td colspan="8">No cases match these filters.</td></tr>`;
    $("#count", el).textContent = `${rows.length} of ${cases.length} cases`;
    el._rows = rows;
  };
  draw();

  $("#statusTabs", el).addEventListener("click", (e) => { const b = e.target.closest("[data-s]"); if (b) { f.status = b.dataset.s; draw(); } });
  $("#fPrio", el).addEventListener("change", (e) => { f.prio = e.target.value; draw(); });
  $("#fType", el).addEventListener("change", (e) => { f.type = e.target.value; draw(); });
  $("#fDept", el).addEventListener("change", (e) => { f.dept = e.target.value; draw(); });
  $("#fQ", el).addEventListener("input", (e) => { f.q = e.target.value.trim().toLowerCase(); draw(); });
  const go = (tr) => tr && (location.hash = tr.dataset.href);
  $("#rows", el).addEventListener("click", (e) => go(e.target.closest("tr[data-href]")));
  $("#rows", el).addEventListener("keydown", (e) => { if (e.key === "Enter") go(e.target.closest("tr[data-href]")); });
  $("#exportCsv", el).addEventListener("click", () => {
    const head = ["case", "type", "priority", "status", "department", "drive", "lat", "lon", "detected", "confidence"];
    const lines = (el._rows || []).map((c) => [c.number, c.label, c.priority, c.status, c.department, c.run_id, c.centroid[1], c.centroid[0], c.first_seen, c.confidence]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" }));
    a.download = "hazard-cases.csv";
    a.click();
  });
}
