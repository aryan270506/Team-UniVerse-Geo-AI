// Contributors & coins (admin): every registered user, their drives and coin ledger, with
// adjust / revoke / disable actions. #/users lists everyone; #/users/<id> opens one user.
import { $, api, date, esc, pageHead, toast } from "../core.js";

const STATUS = { queued: "Queued", processing: "Analysing", done: "Done", rejected: "Not rewarded", failed: "Failed" };
const SRC = { upload: "Upload", record: "Recorded", live: "Live" };
const KIND = { earn: "Footage", bonus: "Hazard bonus", revoke: "Revoked", adjust: "Adjustment", estimate: "Estimate", redeem: "Voucher", refund: "Refund" };

export default async function render(el, [userId]) {
  const data = await api("/api/admin/users");
  const users = data.users.filter((u) => u.role === "user");
  const f = { q: "" };
  el.innerHTML = `
    ${pageHead("07", "Contributors", "Contributors & coins", `<a class="btn primary" href="#/market"><i data-lucide="gift"></i>Rewards marketplace</a>`)}
    <div class="g-3" style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px">
      ${[["Contributors", users.length], ["Drives contributed", users.reduce((a, u) => a + u.drives, 0)],
         ["Minutes of footage", Math.round(users.reduce((a, u) => a + u.minutes, 0))], ["Coins issued", data.coins_issued]]
        .map(([k, v]) => `<section class="card"><div class="label">${k}</div><div style="font-size:var(--fs-2xl);font-weight:600">${Number(v).toLocaleString()}</div></section>`).join("")}
    </div>
    <div class="filters"><label class="field search"><span>Search</span><input class="input line" id="q" placeholder="Name or email" type="search"></label></div>
    <div class="table-wrap cards"><table class="table">
      <thead><tr><th>Contributor</th><th>Drives</th><th>Minutes</th><th>Hazards found</th><th>Coins</th><th>Pending</th><th>Last drive</th><th>Joined</th><th>Status</th></tr></thead>
      <tbody id="rows"></tbody></table></div>
    <dialog id="userDlg" style="width:min(820px,calc(100vw - 32px))" aria-labelledby="uTitle"></dialog>`;

  const draw = () => {
    const rows = users.filter((u) => !f.q || `${u.name} ${u.email}`.toLowerCase().includes(f.q));
    $("#rows", el).innerHTML = rows.map((u) => `<tr data-id="${u.id}" tabindex="0">
      <td><div class="id">${esc(u.name)}</div><div class="muted" style="font-size:12px">${esc(u.email)}</div></td>
      <td class="num">${u.drives}</td><td class="num">${u.minutes}</td><td class="num">${u.hazards}</td>
      <td class="num" style="font-weight:700">${u.balance}</td><td class="num muted">${u.pending || "–"}</td>
      <td class="muted">${u.last_drive ? date(u.last_drive) : "–"}</td><td class="muted">${date(u.created_at)}</td>
      <td>${u.disabled ? `<span class="chip warn"><i></i>Disabled</span>` : `<span class="chip st-resolved"><i></i>Active</span>`}</td></tr>`).join("")
      || `<tr class="empty-row"><td colspan="9">No contributors yet. Share <b>${esc(location.origin)}/register.html</b>.</td></tr>`;
  };
  draw();
  $("#q", el).addEventListener("input", (e) => { f.q = e.target.value.trim().toLowerCase(); draw(); });
  const open = (id) => { location.hash = `#/users/${id}`; };
  $("#rows", el).addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) open(tr.dataset.id); });
  $("#rows", el).addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr && e.key === "Enter") open(tr.dataset.id); });

  const dlg = $("#userDlg", el);
  dlg.addEventListener("close", () => { if (location.hash.startsWith("#/users/")) history.replaceState(null, "", "#/users"); });
  if (userId) showUser(dlg, Number(userId));
}

async function showUser(dlg, id) {
  let d = await api(`/api/admin/users/${id}`);
  const post = async (path, body, msg) => {
    try {
      d = await api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      toast(msg);
      draw();
    } catch (e) { toast(`Couldn't save: ${esc(e.message)}`); }
  };
  const draw = () => {
    const u = d.user;
    dlg.innerHTML = `
      <div class="dlg-head"><h2 id="uTitle">${esc(u.name)}</h2><button type="button" class="x" data-close aria-label="Close"><i data-lucide="x"></i></button></div>
      <div class="dlg-body">
        <p class="muted" style="margin:0">${esc(u.email)} · joined ${date(u.created_at)} ${u.disabled ? `· <b style="color:var(--p1)">disabled</b>` : ""}</p>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
          ${[["Balance", d.balance], ["Pending", d.pending], ["Earned all-time", d.earned]].map(([k, v]) =>
            `<div class="card" style="padding:14px"><div class="label">${k}</div><b style="font-size:var(--fs-xl)">${v}</b></div>`).join("")}
        </div>
        <form id="adj" class="row three" style="align-items:end">
          <label class="field"><span>Adjust coins (±)</span><input class="input" name="amount" type="number" step="1" required></label>
          <label class="field"><span>Reason</span><input class="input" name="note" minlength="3" maxlength="200" required placeholder="E.g. bonus for flood footage"></label>
          <button class="btn primary" type="submit">Apply</button>
        </form>
        <h3 style="margin:8px 0 0">Drives</h3>
        <div class="table-wrap" style="box-shadow:none;padding:0"><table class="table">
          <thead><tr><th>Drive</th><th>Source</th><th>Status</th><th>Length</th><th>Hazards</th><th>Coins</th><th></th></tr></thead>
          <tbody>${d.drives.map((x) => `<tr>
            <td><a href="#/command/${encodeURIComponent(x.run_id)}">${esc(x.name)}</a><div class="muted" style="font-size:12px">${date(x.created_at)}${x.note ? ` · ${esc(x.note)}` : ""}</div></td>
            <td>${SRC[x.source]}</td><td>${STATUS[x.status] || x.status}</td><td class="num">${x.duration_s ? `${Math.round(x.duration_s)} s` : "–"}</td>
            <td class="num">${x.hazards ?? "–"}</td><td class="num" style="font-weight:700">${x.coins}${x.coins_pending ? ` <span class="muted">(+${x.coins_pending})</span>` : ""}</td>
            <td>${x.coins > 0 ? `<button class="btn sm" data-revoke="${esc(x.run_id)}">Revoke</button>` : ""}</td></tr>`).join("")
            || `<tr class="empty-row"><td colspan="7">No drives yet.</td></tr>`}</tbody></table></div>
        <h3 style="margin:8px 0 0">Coin ledger</h3>
        <ol class="feed">${d.ledger.slice(0, 30).map((x) => `<li class="${x.amount < 0 ? "status" : "note"}"><div>
          <b>${x.amount > 0 ? "+" : ""}${x.amount}</b> ${KIND[x.kind] || x.kind}${x.status === "pending" ? " (pending)" : ""}${x.drive_name ? ` · ${esc(x.drive_name)}` : ""}
          <div class="meta">${esc(x.note || "")} · ${date(x.created_at)}</div></div></li>`).join("") || `<li><div class="muted">No coins yet.</div></li>`}</ol>
        <div style="display:flex;justify-content:flex-end">
          <button class="btn ${u.disabled ? "" : "danger"}" id="dis">${u.disabled ? "Re-enable account" : "Disable account"}</button>
        </div>
      </div>`;
    window.lucide?.createIcons({ root: dlg });
    dlg.querySelector("[data-close]").onclick = () => dlg.close();
    dlg.querySelector("#adj").onsubmit = (e) => {
      e.preventDefault();
      const amount = parseInt(e.target.amount.value, 10);
      if (!amount) return;
      post(`/api/admin/users/${id}/adjust`, { amount, note: e.target.note.value }, `${amount > 0 ? "Added" : "Removed"} ${Math.abs(amount)} coins`);
    };
    dlg.querySelectorAll("[data-revoke]").forEach((b) => b.onclick = () => {
      const note = prompt("Reason for revoking this drive's coins?", "Footage not usable");
      if (note) post(`/api/admin/drives/${encodeURIComponent(b.dataset.revoke)}/revoke`, { note }, "Coins revoked");
    });
    dlg.querySelector("#dis").onclick = () => {
      if (u.disabled || confirm(`Disable ${u.name}? They will be signed out and can't log in.`))
        post(`/api/admin/users/${id}/disable`, { disabled: !u.disabled }, u.disabled ? "Account re-enabled" : "Account disabled");
    };
  };
  draw();
  if (!dlg.open) dlg.showModal();
}
