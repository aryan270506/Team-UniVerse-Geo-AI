// TerraTrace contributor app: hash router + shared bits (balance pill, drive rows, status chips).
import { $, $$, api, esc, icons, loadMe } from "/js/core.js";

const routes = {
  home: () => import("./pages/home.js"),
  drives: () => import("./pages/drives.js"),
  map: () => import("./pages/map.js"),
  record: () => import("./pages/record.js"),
  upload: () => import("./pages/upload.js"),
  live: () => import("./pages/live.js"),
  rewards: () => import("./pages/rewards.js"),
  market: () => import("./pages/market.js"),
  vouchers: () => import("./pages/market.js"),
  account: () => import("./pages/account.js"),
};
const TAB_OF = { upload: "home", live: "home", drives: "home", rewards: "market", vouchers: "market" };

let cleanup = null, seq = 0;
async function navigate() {
  const [path, qs = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [name = "home", ...params] = path.split("/").map(decodeURIComponent);
  const route = routes[name] ? name : "home";
  const my = ++seq;
  if (cleanup) { try { cleanup(); } catch { /* page already gone */ } cleanup = null; }
  $$(".u-tabs a").forEach((a) => a.classList.toggle("on", a.dataset.route === (TAB_OF[route] || route)));
  const outlet = $("#outlet");
  outlet.innerHTML = "";
  const mod = await routes[route]();
  if (my !== seq) return;
  try {
    cleanup = (await mod.default(outlet, params, new URLSearchParams(qs))) || null;
  } catch (e) {
    console.error(e);
    outlet.innerHTML = `<p class="err-box">Couldn't load this page: ${esc(e.message)}</p>`;
  }
  icons(outlet);
  window.scrollTo(0, 0);
}

export async function refreshBalance() {
  const c = await api("/api/me/coins").catch(() => null);
  if (c) $("#bal").textContent = c.balance.toLocaleString();
  return c;
}

// ---------- shared view helpers
export const SRC_ICON = { upload: "upload", record: "video", live: "radio" };
export const SRC_LABEL = { upload: "Upload", record: "Recorded", live: "Live" };
export function driveStatus(d) {
  if (d.status === "queued") return `<span class="chip wait"><i></i>Queued${d.queue ? ` #${d.queue}` : ""}</span>`;
  if (d.status === "processing") return `<span class="chip run"><i></i>Analysing</span>`;
  if (d.status === "done") return `<span class="chip ok"><i></i>Done</span>`;
  if (d.status === "rejected") return `<span class="chip warn"><i></i>Not rewarded</span>`;
  return `<span class="chip warn"><i></i>Failed</span>`;
}
export const fmtDur = (s) => (s == null ? "–" : s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);
export function driveRow(d) {
  const coins = d.coins ? `+${d.coins}` : d.coins_pending ? `≈${d.coins_pending}` : "0";
  return `<a class="drive-row" href="#/drives/${encodeURIComponent(d.run_id)}">
    <span class="src"><i data-lucide="${SRC_ICON[d.source] || "video"}"></i></span>
    <span style="min-width:0"><span class="t">${esc(d.name)}</span>
      <span class="m">${new Date(d.created_at).toLocaleDateString([], { day: "numeric", month: "short" })} · ${fmtDur(d.duration_s)}${d.hazards != null ? ` · ${d.hazards} hazard${d.hazards === 1 ? "" : "s"}` : ""}</span></span>
    <span class="c"><span><span class="coin" style="display:inline-grid;width:18px;height:18px;font-size:10px;vertical-align:-3px">T</span> ${coins}</span>${driveStatus(d)}</span></a>`;
}

// ---------- boot: must be signed in (the server already redirects anonymous visitors)
(async () => {
  try { await loadMe(); } catch { return; }
  refreshBalance();
  window.addEventListener("hashchange", navigate);
  navigate();
})();
