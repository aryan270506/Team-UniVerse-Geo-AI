// Rewards marketplace (admin): voucher stock per denomination, add codes, prices, and the
// redemption log with void & refund.
import { $, api, date, esc, pageHead, toast } from "../core.js";

const inr = (n) => `₹${Number(n).toLocaleString("en-IN")}`;

export default async function render(el) {
  let m = await api("/api/admin/market");
  const draw = () => {
    const stock = m.items.reduce((a, i) => a + i.stock, 0);
    el.innerHTML = `
      ${pageHead("08", "Marketplace", "Rewards marketplace", `<a class="btn" href="#/users"><i data-lucide="users"></i>Contributors</a>`)}
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px">
        ${[["Vouchers delivered", m.redeemed.toLocaleString("en-IN")], ["Value delivered", inr(m.redeemed_inr)], ["Codes in stock", stock],
           ["Waiting for a code", m.pending]].map(([k, v], n) =>
          `<section class="card"><div class="label">${k}</div><div style="font-size:var(--fs-2xl);font-weight:600;${n === 3 && v ? "color:var(--p1)" : ""}">${v}</div></section>`).join("")}
      </div>
      <div class="section-head"><h2>Catalog &amp; stock</h2><span class="label">${m.rate} coins = ₹1 · codes are encrypted at rest</span></div>
      <div class="table-wrap cards" style="margin-bottom:24px"><table class="table">
        <thead><tr><th>Reward</th><th>Price (coins)</th><th>In stock</th><th>Waiting</th><th>Issued</th><th>Voided</th><th>Status</th><th></th></tr></thead>
        <tbody>${m.items.map((i) => `<tr>
          <td><b>${esc(i.title)}</b> ${inr(i.value_inr)}<div class="muted" style="font-size:12px">${esc(i.brand)}</div></td>
          <td><input class="input" type="number" min="1" step="1" value="${i.coins}" data-price="${i.id}" style="width:120px;height:36px" aria-label="Coin price"></td>
          <td class="num" style="font-weight:700">${i.stock}${i.low ? ` <span class="chip warn"><i></i>Low</span>` : ""}</td>
          <td class="num" style="${i.pending ? "color:var(--p1);font-weight:700" : ""}">${i.pending}</td>
          <td class="num">${i.issued}</td><td class="num">${i.void}</td>
          <td><label style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" data-active="${i.id}" ${i.active ? "checked" : ""}>${i.active ? "On sale" : "Hidden"}</label></td>
          <td><button class="btn sm primary" data-add="${i.id}"><i data-lucide="plus"></i>Add codes</button></td></tr>`).join("")}</tbody></table></div>
      <div class="section-head"><h2>Recent redemptions</h2><span class="label">Waiting requests are filled automatically when you add codes for that voucher</span></div>
      <div class="table-wrap cards"><table class="table">
        <thead><tr><th>When</th><th>Contributor</th><th>Voucher</th><th>Code</th><th>Coins</th><th>Opened</th><th>Status</th><th></th></tr></thead>
        <tbody>${m.recent.map((r) => `<tr>
          <td class="muted">${date(r.created_at)}</td><td>${esc(r.user)}<div class="muted" style="font-size:12px">${esc(r.email)}</div></td>
          <td>${esc(r.brand)} ${inr(r.value_inr)}</td><td class="coord">${esc(r.code_masked || "")}</td>
          <td class="num">${r.coins.toLocaleString("en-IN")}</td><td class="num">${r.views}×</td>
          <td>${r.status === "void" ? `<span class="chip warn" title="${esc(r.void_reason || "")}"><i></i>Refunded</span>`
            : r.status === "pending" ? `<span class="chip st-new"><i></i>Waiting</span>` : `<span class="chip st-resolved"><i></i>Issued</span>`}</td>
          <td style="white-space:nowrap">${r.status === "pending" ? `<button class="btn sm primary" data-fulfil="${r.id}">Fill with code</button> <button class="btn sm" data-void="${r.id}">Reject &amp; refund</button>`
            : r.status === "issued" ? `<button class="btn sm" data-void="${r.id}">Void &amp; refund</button>` : ""}</td></tr>`).join("")
          || `<tr class="empty-row"><td colspan="8">No vouchers redeemed yet.</td></tr>`}</tbody></table></div>
      <dialog id="codesDlg"></dialog>`;
  };
  draw();

  const save = async (path, body, msg, method = "POST") => {
    try {
      m = await api(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      draw();
      if (msg) toast(msg);
    } catch (e) { toast(`Couldn't save: ${esc(e.message)}`); }
  };
  el.addEventListener("change", (e) => {
    const p = e.target.closest("[data-price]"), a = e.target.closest("[data-active]");
    if (p && Number(p.value) > 0) save(`/api/admin/market/${p.dataset.price}`, { coins: Number(p.value) }, "Price updated", "PATCH");
    if (a) save(`/api/admin/market/${a.dataset.active}`, { active: a.checked }, a.checked ? "Back on sale" : "Hidden from the marketplace", "PATCH");
  });
  el.addEventListener("click", (e) => {
    const add = e.target.closest("[data-add]"), v = e.target.closest("[data-void]"), f = e.target.closest("[data-fulfil]");
    if (f) openFulfil(el, m.recent.find((r) => r.id === Number(f.dataset.fulfil)), (next) => { m = next; draw(); });
    if (v) {
      const reason = prompt("Why void this voucher? The contributor's coins are refunded and the code is retired.", "Code rejected by Amazon");
      if (reason && reason.trim().length >= 3) save(`/api/admin/redemptions/${v.dataset.void}/void`, { reason }, "Voided and refunded");
    }
    if (add) openCodes(el, m.items.find((i) => i.id === Number(add.dataset.add)), async () => { m = await api("/api/admin/market"); draw(); });
  });
}

function openFulfil(el, r, done) {
  const dlg = $("#codesDlg", el);
  dlg.innerHTML = `
    <div class="dlg-head"><h2>Fill ${esc(r.brand)} ${inr(r.value_inr)} for ${esc(r.user)}</h2><button type="button" class="x" data-close aria-label="Close"><i data-lucide="x"></i></button></div>
    <form class="dlg-body" id="fForm">
      <p class="muted" style="margin:0">Enter the gift-card code you bought for this request. It is encrypted and shown only to ${esc(r.user)}.</p>
      <label class="field"><span>Claim code</span><input class="input" name="code" required minlength="8" maxlength="40" autocomplete="off"></label>
      <div class="row"><label class="field"><span>PIN (optional)</span><input class="input" name="pin" maxlength="20" autocomplete="off"></label>
        <label class="field"><span>Expiry (optional)</span><input class="input" type="date" name="expiry"></label></div>
      <div style="display:flex;justify-content:flex-end"><button class="btn primary" type="submit">Deliver voucher</button></div>
    </form>`;
  window.lucide?.createIcons({ root: dlg });
  dlg.showModal();
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("#fForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const next = await api(`/api/admin/redemptions/${r.id}/fulfil`, { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ code: e.target.code.value, pin: e.target.pin.value, expiry: e.target.expiry.value }) });
      dlg.close();
      toast(`Voucher delivered to ${esc(r.user)}`);
      done(next);
    } catch (ex) { toast(`Couldn't deliver: ${esc(ex.message)}`); }
  };
}

function openCodes(el, item, done) {
  const dlg = $("#codesDlg", el);
  dlg.innerHTML = `
    <div class="dlg-head"><h2>Add ${esc(item.brand)} ${inr(item.value_inr)} codes</h2><button type="button" class="x" data-close aria-label="Close"><i data-lucide="x"></i></button></div>
    <form class="dlg-body" id="codesForm">
      <p class="muted" style="margin:0">One code per line, optionally <code>code,pin,expiry</code> (expiry as YYYY-MM-DD). Paste, or load the CSV your gift-card supplier sent. Duplicates and already-used codes are skipped.</p>
      <label class="field"><span>Codes</span><textarea class="textarea" name="text" rows="8" required placeholder="AQ12-BC34-DE56XY,1234,2027-12-31"></textarea></label>
      <div class="row">
        <label class="field"><span>Or load a CSV</span><input type="file" accept=".csv,.txt" id="csv"></label>
        <label class="field"><span>Batch label</span><input class="input" name="batch" maxlength="60" placeholder="E.g. Oct 2026 purchase"></label>
      </div>
      <p class="muted" id="result" hidden></p>
      <div style="display:flex;justify-content:flex-end"><button class="btn primary" type="submit" id="go">Add codes</button></div>
    </form>`;
  window.lucide?.createIcons({ root: dlg });
  dlg.showModal();
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("#csv").onchange = async (e) => { const f = e.target.files[0]; if (f) dlg.querySelector("[name=text]").value = await f.text(); };
  dlg.querySelector("#codesForm").onsubmit = async (e) => {
    e.preventDefault();
    const go = dlg.querySelector("#go");
    go.disabled = true;
    try {
      const r = await api(`/api/admin/market/${item.id}/codes`, { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: e.target.text.value, batch: e.target.batch.value }) });
      const res = dlg.querySelector("#result");
      res.hidden = false;
      res.innerHTML = `<b>${r.added} added</b> · ${r.duplicates} duplicate${r.duplicates === 1 ? "" : "s"} skipped · ${r.invalid_count} invalid` +
        (r.invalid.length ? `<br>${r.invalid.map(esc).join("<br>")}` : "");
      e.target.text.value = "";
      toast(`${r.added} codes added${r.filled ? ` · ${r.filled} waiting request${r.filled === 1 ? "" : "s"} filled` : ""}`);
      done();
    } catch (ex) { toast(`Couldn't add codes: ${esc(ex.message)}`); }
    go.disabled = false;
  };
}
