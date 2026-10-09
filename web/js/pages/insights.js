// Insights: network-level numbers for managers (what used to be the overview).
import { ago, api, avatar, bars, esc, pageHead, STATUSES, STATUS_LABEL } from "../core.js";

export default async function render(el) {
  const o = await api("/api/overview");
  const h = o.network_health;
  const tone = h.score >= 70 ? "" : h.score >= 55 ? "mid" : "bad";
  el.innerHTML = `
    ${pageHead("", "Insights", "Road network health", `<a class="btn" href="#/cases"><i data-lucide="layout-grid"></i>All cases</a>`)}
    <section class="stats" aria-label="Key figures" style="margin-bottom:20px">
      <a class="stat" href="#/cases"><span class="ic"><i data-lucide="folder-open"></i></span><span class="label">Open cases</span><span class="v num">${o.open}</span></a>
      <a class="stat signal" href="#/cases?prio=P1"><span class="ic"><i data-lucide="siren"></i></span><span class="label">Critical (P1) open</span><span class="v num">${o.p1_open}</span></a>
      <a class="stat" href="#/board"><span class="ic"><i data-lucide="loader"></i></span><span class="label">In progress</span><span class="v num">${o.by_status.in_progress}</span></a>
      <a class="stat" href="#/cases?status=resolved"><span class="ic"><i data-lucide="circle-check"></i></span><span class="label">Resolved · 7 days</span><span class="v num">${o.resolved_7d}</span></a>
    </section>

    <div class="g-8-4">
      <div class="stack">
        <section class="card grad-mint">
          <div class="health-big">
            <span class="grade ${tone}" aria-label="Network grade ${h.grade}">${h.grade}</span>
            <div><h2 style="font-size:var(--fs-xl)">Network health ${h.score}/100</h2>
              <p class="muted">${o.km_surveyed} km surveyed across ${o.drives.length} drives · ${o.open} open hazards weighted by severity per km</p></div>
          </div>
        </section>
        <section class="card">
          <div class="section-head"><h2>Open cases by type</h2></div>
          ${bars(Object.entries(o.by_category).sort((a, b) => b[1] - a[1]), { red: (n) => ["Flooding", "Landslide", "Power line down", "Fallen tree"].includes(n) })}
        </section>
        <section class="card">
          <div class="section-head"><h2>Drives</h2><a class="link-btn" href="#/drives">All drives</a></div>
          ${bars(o.drives.filter((d) => !d.id.startsWith("eval-")).slice(-8).reverse().map((d) => [`${d.id} · ${d.health.grade}`, d.hazards || 0]))}
        </section>
      </div>
      <aside class="stack">
        <section class="card">
          <div class="section-head"><h2>Pipeline</h2></div>
          ${bars(STATUSES.map((s) => [STATUS_LABEL[s], o.by_status[s] || 0]), { red: (n) => n === "New" })}
        </section>
        <section class="card">
          <div class="section-head"><h2>Departments</h2><span class="label">open</span></div>
          ${bars(Object.entries(o.by_department).sort((a, b) => b[1] - a[1]))}
        </section>
        <section class="card">
          <div class="section-head"><h2>Activity</h2></div>
          <ol class="feed">${o.activity.slice(0, 8).map((a) => {
            const [run, hid] = a.case_id.split("/");
            return `<li class="${a.kind}"><div><a href="#/cases/${encodeURIComponent(run)}/${encodeURIComponent(hid)}">${esc(a.number)}</a> ${esc(a.text)}
              <div class="meta">${esc(a.actor)} · ${ago(a.ts)}</div></div></li>`;
          }).join("") || `<li><div class="muted">No activity yet.</div></li>`}</ol>
        </section>
      </aside>
    </div>`;
}
