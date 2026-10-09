// Rewards marketplace: spend coins on Amazon.in gift vouchers (#/market) and see the ones
// you've redeemed (#/vouchers, #/vouchers/<id> reveals the code).
import { api, esc, toast } from "/js/core.js";
import { refreshBalance } from "/app/app.js";

const inr = (n) => `₹${Number(n).toLocaleString("en-IN")}`;
const ART = { "Amazon.in": "/img/vouchers/amazon.svg" };
const face = (v) => `<div class="gc-face" style="background-image:url('${ART[v.brand] || ART["Amazon.in"]}')">
  <span class="gc-brand">${esc(v.brand)}</span><span class="gc-val">${inr(v.value_inr)}</span><span class="gc-kind">Gift Card</span></div>`;
const coins = (n) => Number(n).toLocaleString("en-IN");

export default async function render(el, params, query, route) {
  return (location.hash.startsWith("#/vouchers") ? vouchers : market)(el, params);
}

function card(i) {
  return `<article class="gc">
    ${face(i)}
    <div class="gc-body">
      <div class="gc-price"><span class="coin" style="width:20px;height:20px;font-size:10px">T</span><b>${coins(i.coins)}</b> coins</div>
      <span class="label">${i.instant ? "⚡ Instant delivery" : "Delivered within 48 h"}</span>
      ${i.affordable ? "" : `<span class="label">${coins(i.short_by)} more coins needed</span>`}
      <button class="btn ${i.affordable ? "primary" : ""} block" data-redeem="${i.id}" ${i.affordable ? "" : "disabled"}>
        ${i.affordable ? "Redeem" : "Keep driving"}</button>
    </div></article>`;
}

async function market(el) {
  const m = await api("/api/me/market");
  el.innerHTML = `
    <section class="hero">
      <div class="bal"><span class="coin lg">T</span><div><b>${coins(m.balance)}</b><br><span>coins to spend · worth ${inr(Math.floor(m.balance / m.rate))}${m.pending ? ` · ${m.pending} pending` : ""}</span></div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn sm" href="#/vouchers"><i data-lucide="ticket"></i>My vouchers</a><a class="btn sm" href="#/rewards"><i data-lucide="trophy"></i>Leaderboard &amp; history</a></div>
    </section>
    <section>
      <div class="section-head"><h2>Amazon.in vouchers</h2><span class="label">${m.rate} coins = ₹1</span></div>
      <div class="gc-grid">${m.items.map(card).join("") || `<div class="card empty">No rewards available right now.</div>`}</div>
      <p class="label" style="margin:12px 0 0">Delivered instantly as an Amazon.in gift card code. Up to ${m.limits.per_day} vouchers a day and ${inr(m.limits.inr_per_month)} a month. Redeemed coins can't be returned.</p>
    </section>
    <dialog id="dlg" class="sheet"></dialog>`;
  const dlg = el.querySelector("#dlg");
  el.querySelectorAll("[data-redeem]").forEach((b) => b.addEventListener("click", () => {
    const i = m.items.find((x) => x.id === Number(b.dataset.redeem));
    dlg.innerHTML = `<form method="dialog" class="form">
      <h2 style="margin:0">Redeem ${inr(i.value_inr)} voucher?</h2>
      <p style="margin:0">Spend <b>${coins(i.coins)} coins</b> for an ${esc(i.brand)} gift card worth <b>${inr(i.value_inr)}</b>. You'll have ${coins(m.balance - i.coins)} coins left. This can't be undone.</p>
      ${i.instant ? "" : `<p class="label" style="margin:0">This voucher is being restocked: your code will appear under My vouchers within ${m.fulfil_hours} hours. If we can't deliver it, your coins are refunded.</p>`}
      <p class="err-box" id="err" hidden></p>
      <div style="display:grid;grid-template-columns:1fr 2fr;gap:10px"><button class="btn" value="cancel">Cancel</button><button class="btn primary" id="yes" value="">Redeem now</button></div></form>`;
    dlg.showModal();
    dlg.querySelector("#yes").addEventListener("click", async (e) => {
      e.preventDefault();
      e.currentTarget.disabled = true;
      try {
        const v = await api(`/api/me/market/${i.id}/redeem`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirm: true }) });
        refreshBalance();
        showCode(dlg, v, () => market(el));
      } catch (ex) {
        const err = dlg.querySelector("#err");
        err.textContent = ex.message;
        err.hidden = false;
        e.currentTarget.disabled = false;
      }
    });
  }));
}

function showCode(dlg, v, onClose) {
  dlg.innerHTML = `<div class="form">
    <div style="border-radius:18px;overflow:hidden">${face(v)}</div>
    <h2 style="margin:0">${v.status === "issued" ? "Your voucher is ready" : v.status === "pending" ? "Voucher requested" : "Voucher refunded"}</h2>
    ${v.status === "pending" ? `<p style="margin:0">Your coins are reserved. The ${inr(v.value_inr)} code will appear here under <b>My vouchers</b> within ${v.fulfil_hours} hours.
      If it can't be delivered, the coins come back to you automatically.</p>` : v.status === "issued" ? `
      <div class="code-box"><span class="label">Claim code</span><b id="code">${esc(v.code)}</b>${v.pin ? `<span class="label">PIN ${esc(v.pin)}</span>` : ""}</div>
      ${v.expires_on ? `<span class="label">Valid until ${new Date(v.expires_on).toLocaleDateString([], { dateStyle: "medium" })}</span>` : ""}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <button class="btn" id="copy"><i data-lucide="copy"></i>Copy code</button>
        <a class="btn primary" href="${esc(v.redeem_url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Add to Amazon</a>
      </div>
      <p class="label" style="margin:0">On Amazon.in: Your Account → Amazon Pay → Add Gift Card, then paste the code. Keep it private: anyone with the code can use it.</p>`
      : `<p style="margin:0">${esc(v.void_reason || "")}. Your coins were refunded.</p>`}
    <button class="btn block" id="close">Done</button></div>`;
  window.lucide?.createIcons({ root: dlg });
  if (!dlg.open) dlg.showModal();
  dlg.querySelector("#copy")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(v.code); toast("Code copied"); } catch { toast("Copy blocked: select the code and copy it"); }
  });
  dlg.querySelector("#close").addEventListener("click", () => { dlg.close(); onClose?.(); });
}

async function vouchers(el, [id]) {
  const { vouchers: list } = await api("/api/me/redemptions");
  el.innerHTML = `
    <div><a class="label" href="#/market">← Marketplace</a><h1 class="u-h1">My vouchers</h1></div>
    <div class="list">${list.map((v) => `<button class="drive-row" data-id="${v.id}" style="border:0;font:inherit;text-align:left;width:100%;cursor:pointer">
        <span class="src"><i data-lucide="ticket"></i></span>
        <span style="min-width:0"><span class="t">${esc(v.brand)} ${inr(v.value_inr)}</span>
          <span class="m">${new Date(v.created_at).toLocaleDateString([], { dateStyle: "medium" })} · ${esc(v.code_masked || "")}</span></span>
        <span class="c">−${coins(v.coins)}${v.status === "void" ? `<span class="chip warn"><i></i>Refunded</span>`
          : v.status === "pending" ? `<span class="chip run"><i></i>Processing</span>` : `<span class="chip ok"><i></i>Show code</span>`}</span></button>`).join("")
      || `<div class="card empty">No vouchers yet.<br><a class="btn primary" style="margin-top:14px" href="#/market">Browse rewards</a></div>`}</div>
    <dialog id="dlg" class="sheet"></dialog>`;
  const dlg = el.querySelector("#dlg");
  const open = async (vid) => {
    try { showCode(dlg, await api(`/api/me/redemptions/${vid}`)); } catch (e) { toast(esc(e.message)); }
  };
  el.querySelectorAll("[data-id]").forEach((b) => b.addEventListener("click", () => open(b.dataset.id)));
  if (id) open(id);
}
