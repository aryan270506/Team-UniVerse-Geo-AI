// App shell: hash router, nav state, top bar (search, alerts, operator), live-session strip.
import { $, $$, ago, api, avatar, caseHref, catTile, esc, icons, initials, operator, statusTag, store } from "./core.js";
import { initDialogs } from "./dialogs.js";

const routes = {
  home: () => import("./pages/home.js"),
  cases: () => import("./pages/cases.js"),
  board: () => import("./pages/board.js"),
  drives: () => import("./pages/drives.js"),
  command: () => import("./pages/command.js"),
  insights: () => import("./pages/insights.js"),
};
const ALIASES = { overview: "home", map: "home" };     // links from earlier versions

let cleanup = null;
let seq = 0;

async function navigate() {
  const [path, qs = ""] = location.hash.replace(/^#\/?/, "").split("?");
  let [name = "home", ...params] = path.split("/").map(decodeURIComponent);
  name = ALIASES[name] || name;
  const query = new URLSearchParams(qs);
  const route = name === "cases" && params.length === 2 ? "case" : (routes[name] ? name : "home");
  const my = ++seq;
  if (cleanup) { try { cleanup(); } catch { /* page already gone */ } cleanup = null; }
  const navRoute = route === "case" ? "cases" : route;
  $$("#nav a").forEach((a) => a.parentElement.classList.toggle("active", a.dataset.route === navRoute));
  const outlet = $("#outlet");
  outlet.innerHTML = "";
  outlet.className = `page page-${route}`;
  const mod = await (route === "case" ? import("./pages/case.js") : routes[route]());
  if (my !== seq) return;                       // user navigated again while loading
  try {
    cleanup = (await mod.default(outlet, params, query)) || null;
  } catch (e) {
    console.error(e);
    outlet.innerHTML = `<p class="error">Couldn't load this page: ${esc(e.message)}</p>`;
  }
  icons(outlet);
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", navigate);

// Lucide: render any <i data-lucide> that pages add later (lists re-render on filter etc.)
let iconQueued = false;
new MutationObserver(() => {
  if (iconQueued || !document.querySelector("i[data-lucide]")) return;
  iconQueued = true;
  requestAnimationFrame(() => { iconQueued = false; icons(); });
}).observe(document.body, { childList: true, subtree: true });

// ---------- top bar: operator
function showOperator() {
  const name = operator();
  $("#userName").textContent = name;
  $("#userAvatar").textContent = initials(name);
}
const op = $("#operator");
try { op.value = localStorage.getItem("hm.operator") || ""; } catch { /* storage blocked */ }
op.addEventListener("input", () => { try { localStorage.setItem("hm.operator", op.value.trim()); } catch { /* ignore */ } showOperator(); });
showOperator();

// ---------- popovers (search results, alerts, user menu)
const pops = { searchPop: null, bellPop: "#bellBtn", userPop: "#userBtn" };
function closePops(except) { Object.keys(pops).forEach((id) => { if (id !== except) $(`#${id}`).hidden = true; }); }
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search, #bellBtn, #bellPop, #userBtn, #userPop")) closePops();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePops(); });

$("#userBtn").addEventListener("click", () => { closePops("userPop"); $("#userPop").hidden = !$("#userPop").hidden; if (!$("#userPop").hidden) op.focus(); });

$("#bellBtn").addEventListener("click", async () => {
  closePops("bellPop");
  const pop = $("#bellPop");
  if (!pop.hidden) { pop.hidden = true; return; }
  const { cases } = await store.cases();
  const p1 = cases.filter((c) => c.status !== "resolved" && c.priority === "P1").slice(0, 6);
  const o = await api("/api/overview").catch(() => ({ activity: [] }));
  pop.innerHTML = `<div class="pop-head">Critical cases <span class="muted">${p1.length}</span></div>
    ${p1.map((c) => `<a class="pop-item" href="${caseHref(c)}">${catTile(c.category, "sm")}<span><span class="t">${esc(c.number)} · ${esc(c.label)}</span><span class="m">${esc(c.department)}</span></span>${statusTag(c.status)}</a>`).join("")
      || `<div class="pop-empty">No open P1 cases.</div>`}
    <div class="pop-head" style="margin-top:4px">Recent activity</div>
    ${o.activity.slice(0, 4).map((a) => `<div class="pop-item" style="cursor:default">${avatar(a.actor, "sm")}<span><span class="t">${esc(a.number)}</span><span class="m">${esc(a.text)}</span></span><span class="m">${ago(a.ts)}</span></div>`).join("")
      || `<div class="pop-empty">No activity yet.</div>`}`;
  pop.hidden = false;
});

// ---------- top bar: search cases
const search = $("#globalSearch");
search.addEventListener("input", async () => {
  const q = search.value.trim().toLowerCase();
  const pop = $("#searchPop");
  if (!q) { pop.hidden = true; return; }
  const { cases } = await store.cases();
  const hits = cases.filter((c) => `${c.number} ${c.label} ${c.department} ${c.run_id} ${c.status}`.toLowerCase().includes(q)).slice(0, 7);
  pop.innerHTML = hits.map((c) => `<a class="pop-item" href="${caseHref(c)}">${catTile(c.category, "sm")}<span><span class="t">${esc(c.number)} · ${esc(c.label)}</span><span class="m">${esc(c.run_id)}</span></span>${statusTag(c.status)}</a>`).join("")
    || `<div class="pop-empty">No cases match “${esc(q)}”.</div>`;
  pop.hidden = false;
});
search.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { const a = $("#searchPop a"); if (a) { location.hash = a.getAttribute("href"); closePops(); search.blur(); } }
});
$("#searchPop").addEventListener("click", () => { closePops(); search.value = ""; });

// ---------- live sessions running anywhere
async function pollLive() {
  const strip = $("#liveStrip");
  const active = await api("/api/live").catch(() => []);
  const s = active[0];
  $("#liveDot").hidden = !s;
  const watching = s && location.hash.startsWith("#/command/live/") && location.hash.endsWith(encodeURIComponent(s.id));
  if (!s || watching) { strip.hidden = true; return; }
  const kind = { device: "Phone camera", replay: "Live replay", batch: "Drive processing" }[s.mode] || "Live";
  strip.innerHTML = `<span class="rec" aria-hidden="true"></span><span>${kind} session running</span>
    <a class="btn sm" href="#/command/live/${encodeURIComponent(s.id)}">Watch live</a>`;
  strip.hidden = false;
}
setInterval(pollLive, 5000);

initDialogs();
icons();
store.cases().catch(() => {});
pollLive();
if (!location.hash) location.hash = "#/home";
else navigate();
