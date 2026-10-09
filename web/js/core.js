// Shared helpers: API, router, case store, formatting, icons, toasts.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const CAT = {
  pothole: { label: "Pothole", glyph: "P", icon: "circle-dot" }, crack: { label: "Crack", glyph: "C", icon: "zap" },
  alligator_crack: { label: "Alligator crack", glyph: "A", icon: "grid-3x3" }, flooding: { label: "Flooding", glyph: "W", icon: "waves" },
  fallen_tree: { label: "Fallen tree", glyph: "T", icon: "tree-pine" }, power_line: { label: "Power line down", glyph: "E", icon: "utility-pole" },
  debris: { label: "Debris", glyph: "D", icon: "boxes" }, landslide: { label: "Landslide", glyph: "L", icon: "mountain" },
  blockage: { label: "Blockage", glyph: "B", icon: "construction" },
};
export const STATUSES = ["new", "verified", "assigned", "in_progress", "resolved"];
export const STATUS_LABEL = { new: "New", verified: "Verified", assigned: "Assigned", in_progress: "In progress", resolved: "Resolved" };
export const PRIO_OF = { high: "P1", medium: "P2", low: "P3" };
export const ACTION = {
  pothole: "Schedule patching; mark for 2-wheelers", crack: "Add to resurfacing plan", alligator_crack: "Inspect base layer; resurface",
  flooding: "Close road / divert traffic; deploy pumps", fallen_tree: "Dispatch tree-clearing crew", power_line: "Alert power utility; cordon off",
  debris: "Dispatch clearing crew", landslide: "Close road; dispatch earth-movers", blockage: "Verify barricade; traffic advisory",
};
export const BLOCKING = new Set(["flooding", "landslide", "fallen_tree", "power_line", "blockage", "debris"]);

export async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

export const icons = (root) => window.lucide?.createIcons({ attrs: { "stroke-width": 2.25 }, ...(root ? { root } : {}) });

export function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `<span>${msg}</span>`;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

export const operator = () => {
  try { return localStorage.getItem("hm.operator") || "Control room"; } catch { return "Control room"; }
};

// ---------- formatting
export const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
export const coord = ([lon, lat]) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
export function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(s)) return "—";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 60) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}
export const date = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");

export const prioTag = (p) => `<span class="chip prio-${p}" aria-label="Priority ${p}"><i></i>${p}</span>`;
export const statusTag = (s) => `<span class="chip st-${s}"><i></i>${STATUS_LABEL[s] || s}</span>`;
export const catTile = (category, size = "") => `<span class="tile ${size} t-${category}" aria-hidden="true"><i data-lucide="${CAT[category]?.icon || "triangle-alert"}"></i></span>`;
export const glyph = (c) => catTile(c.category, "sm");
export const initials = (name) => String(name || "?").replace(/\(.*?\)/g, "").split(/[\s/]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
export const avatar = (name, size = "") => `<span class="avatar ${size}" aria-hidden="true">${esc(initials(name))}</span>`;

// target resolution time by priority (service level), for the "detected → target fix" track
export const SLA_HOURS = { P1: 24, P2: 72, P3: 168 };
export function sla(c) {
  const start = new Date(c.created_at || c.first_seen).getTime();
  const end = start + SLA_HOURS[c.priority] * 3600e3;
  const now = c.resolved_at ? new Date(c.resolved_at).getTime() : Date.now();
  return { start, end, frac: Math.max(0, Math.min(1, (now - start) / (end - start))), overdue: !c.resolved_at && now > end, done: !!c.resolved_at };
}
export const short = (ms) => new Date(ms).toLocaleString([], { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
export const caseHref = (c) => `#/cases/${encodeURIComponent(c.run_id)}/${encodeURIComponent(c.hazard_id)}`;

// ---------- case store (short-lived cache shared by pages)
export const store = {
  data: null, at: 0,
  async cases(force = false) {
    if (force || !this.data || Date.now() - this.at > 4000) {
      this.data = await api("/api/cases");
      this.at = Date.now();
      const open = this.data.cases.filter((c) => c.status !== "resolved").length;
      const b = $("#navOpen");
      if (b) { b.textContent = open; b.hidden = !open; }
      const p1 = this.data.cases.filter((c) => c.status !== "resolved" && c.priority === "P1").length;
      const bell = $("#bellCount");
      if (bell) { bell.textContent = p1; bell.hidden = !p1; }
    }
    return this.data;
  },
  invalidate() { this.data = null; },
  async update(c, patch) {
    const res = await api(`/api/cases/${encodeURIComponent(c.run_id)}/${encodeURIComponent(c.hazard_id)}`, {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...patch, actor: operator() }),
    });
    this.invalidate();
    return res;
  },
};

// ---------- page header
export function pageHead(_num, section, title, tools = "") {
  return `<header class="page-head">
    <div><span class="crumb">${esc(section)}</span><h1>${title}</h1></div>
    <div class="tools">${tools}</div></header>`;
}

// ---------- health score (same formula as server/cases.py)
export const PENALTY = { high: 25, medium: 10, low: 3 };
export function healthOf(severities, km) {
  const pen = severities.reduce((s, v) => s + (PENALTY[v] || 0), 0);
  const score = Math.max(0, Math.min(100, Math.round(100 - pen / Math.max(km || 0, 0.2))));
  const grade = score >= 85 ? "A" : score >= 70 ? "B" : score >= 55 ? "C" : score >= 40 ? "D" : "F";
  return { score, grade, tone: score >= 70 ? "" : score >= 55 ? "mid" : "bad" };
}

export function bars(items, { red = () => false } = {}) {
  const max = Math.max(1, ...items.map((i) => i[1]));
  return `<div class="bars">${items.map(([name, n]) => `
    <div class="bar" title="${esc(name)}: ${n}"><span class="name">${esc(name)}</span>
      <span class="track"><span class="fill ${red(name) ? "red" : ""}" style="width:${(n / max) * 100}%"></span></span>
      <span class="val">${n}</span></div>`).join("") || `<p class="muted">Nothing yet.</p>`}</div>`;
}

// drives produced by tools/eval.py are test fixtures, not surveys
export const userRuns = (runs) => runs.filter((r) => !r.id.startsWith("eval-"));
