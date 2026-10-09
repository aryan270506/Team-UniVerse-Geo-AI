// Coins: balance, ledger and this week's leaderboard.
import { api, esc } from "/js/core.js";

const KIND = { earn: "Footage", bonus: "Hazard bonus", revoke: "Revoked", adjust: "Adjustment", estimate: "Pending estimate", redeem: "Voucher", refund: "Refund" };

export default async function render(el) {
  const [c, lb] = await Promise.all([api("/api/me/coins"), api("/api/me/leaderboard")]);
  el.innerHTML = `
    <div><a class="label" href="#/market">← Marketplace</a></div>
    <section class="hero">
      <div class="bal"><span class="coin lg">T</span><div><b>${c.balance.toLocaleString()}</b><br><span>coin balance · ${c.earned.toLocaleString()} earned all-time${c.spent ? ` · ${c.spent.toLocaleString()} spent on vouchers` : ""}${c.pending ? ` · ${c.pending} pending` : ""}</span></div></div>
    </section>
    <section class="card">
      <div class="section-head"><h2>Leaderboard</h2><span class="label">This week</span></div>
      <div class="lb">${lb.top.map((r) => `<div class="${r.me ? "me" : ""}"><span class="r">#${r.rank}</span><span>${esc(r.name)}${r.me ? " (you)" : ""}</span><b>${r.coins}</b></div>`).join("")
        || `<p class="label" style="margin:0">No coins earned this week yet. Be the first.</p>`}
        ${lb.me && !lb.top.some((r) => r.me) ? `<div class="me"><span class="r">#${lb.me.rank}</span><span>You</span><b>${lb.me.coins}</b></div>` : ""}</div>
    </section>
    <section class="card">
      <div class="section-head"><h2>History</h2></div>
      <div class="ledger">${c.ledger.map((x) => `<div>
          <span><b>${KIND[x.kind] || x.kind}</b>${x.drive_name ? ` · <a href="#/drives/${encodeURIComponent(x.run_id)}">${esc(x.drive_name)}</a>` : ""}</span>
          <span class="amt ${x.amount < 0 ? "neg" : x.status === "pending" ? "pend" : ""}">${x.amount > 0 ? "+" : ""}${x.amount}${x.status === "pending" ? " pending" : ""}</span>
          <small>${esc(x.note || "")} · ${new Date(x.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</small></div>`).join("")
        || `<p class="label" style="margin:0">Your coin history will appear here after your first drive.</p>`}</div>
    </section>`;
}
