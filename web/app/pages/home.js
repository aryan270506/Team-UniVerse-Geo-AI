import { api, esc } from "/js/core.js";
import { driveRow } from "/app/app.js";

export default async function render(el) {
  const [s, list] = await Promise.all([api("/api/me/summary"), api("/api/me/drives")]);
  const r = s.rules;
  const first = s.user.name.split(" ")[0];
  el.innerHTML = `
    <section class="hero">
      <div><div class="label">Hi ${esc(first)}</div><h1 class="u-h1">Drive. Spot hazards. Earn.</h1></div>
      <div class="bal"><span class="coin lg">T</span><div><b>${s.balance.toLocaleString()}</b><br><span>coins${s.pending ? ` · ${s.pending} pending` : ""}${s.rank ? ` · #${s.rank.rank} this week` : ""}</span></div></div>
      <div class="stats">
        <div><b>${s.drives}</b><span>drives</span></div>
        <div><b>${s.minutes}</b><span>minutes</span></div>
        <div><b>${s.km}</b><span>km mapped</span></div>
        <div><b>${s.hazards}</b><span>hazards</span></div>
      </div>
    </section>

    <section class="actions-3" aria-label="Contribute">
      <a class="action-tile primary" href="#/record"><span class="ic"><i data-lucide="circle-dot"></i></span><b>Record</b><span>Video + GPS together, right here</span></a>
      <a class="action-tile" href="#/live"><span class="ic"><i data-lucide="radio"></i></span><b>Go live</b><span>Stream; hazards appear in real time</span></a>
      <a class="action-tile" href="#/upload"><span class="ic"><i data-lucide="upload"></i></span><b>Upload drive</b><span>Dashcam video + GPX/CSV file</span></a>
    </section>

    <section>
      <div class="section-head"><h2>Recent drives</h2>${list.drives.length ? `<a class="label" href="#/drives">All</a>` : ""}</div>
      <div class="list">${list.drives.slice(0, 4).map(driveRow).join("") ||
        `<div class="card empty">No drives yet. Tap <b>Record</b> on your next trip: mount the phone on the windscreen, landscape, and drive.</div>`}</div>
    </section>

    <section class="card">
      <div class="section-head"><h2>How coins work</h2></div>
      <ul class="how">
        <li><b>1 coin</b> for every ${r.coin_seconds} s of valid driving footage</li>
        <li><b>+${r.hazard_bonus} coins</b> for each hazard our AI confirms on your drive</li>
        <li>Up to <b>${r.drive_cap}</b> coins per drive and <b>${r.daily_cap}</b> per day</li>
        <li>Footage must have GPS, be at least ${r.min_seconds} s long and be recorded while driving (over ${r.min_speed_kmh} km/h on average). Re-uploads of the same video earn nothing.</li>
        <li>Coins show as <b>pending</b> until the AI has analysed your drive.</li>
      </ul>
    </section>`;
  // refresh while something is still being analysed
  if (list.drives.some((d) => d.status === "queued" || d.status === "processing")) {
    const t = setTimeout(() => window.dispatchEvent(new HashChangeEvent("hashchange")), 8000);
    return () => clearTimeout(t);
  }
}
