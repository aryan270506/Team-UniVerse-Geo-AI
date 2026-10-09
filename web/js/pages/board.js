import { $, $$, ago, caseHref, esc, glyph, pageHead, prioTag, store, toast, STATUSES, STATUS_LABEL } from "../core.js";

const PRIO_RANK = { P1: 0, P2: 1, P3: 2 };

export default async function render(el) {
  let { cases } = await store.cases(true);
  el.innerHTML = `
    ${pageHead("03", "Board", "Workflow", `<label class="check"><input type="checkbox" id="hideLow"> Hide P3</label><a class="btn" href="#/cases"><i data-lucide="list"></i>Table view</a>`)}
    <p class="muted" style="margin:-12px 0 20px">Drag a case to move it through the pipeline. Every move is written to the case's activity log.</p>
    <div class="board" id="board">${STATUSES.map((s) => `
      <section class="col" data-status="${s}" aria-label="${STATUS_LABEL[s]}">
        <header class="col-head"><span class="label"><i style="background:var(--st-${s === "in_progress" ? "progress" : s})"></i>${STATUS_LABEL[s]}</span><span class="count num" data-count></span></header>
        <div class="col-body" data-drop="${s}"></div>
      </section>`).join("")}</div>`;

  const card = (c) => `<article class="kcard" draggable="true" data-id="${esc(c.id)}" tabindex="0" aria-label="${esc(c.number)} ${esc(c.label)}">
    <div class="top"><span class="id">${esc(c.number)}</span>${prioTag(c.priority)}</div>
    ${c.snapshot && c.priority !== "P3" ? `<img src="${esc(c.snapshot)}" alt="" loading="lazy">` : ""}
    <div class="t"><span class="kind">${glyph(c)}${esc(c.label)}</span></div>
    <div class="m">${esc(c.department)}</div>
    <div class="m">${esc(c.run_id)} · ${ago(c.first_seen)}</div></article>`;

  const draw = () => {
    const hide = $("#hideLow", el).checked;
    for (const s of STATUSES) {
      const list = cases.filter((c) => c.status === s && !(hide && c.priority === "P3"))
        .sort((a, b) => PRIO_RANK[a.priority] - PRIO_RANK[b.priority] || b.first_seen.localeCompare(a.first_seen));
      $(`[data-drop="${s}"]`, el).innerHTML = list.map(card).join("");
      $(`[data-status="${s}"] [data-count]`, el).textContent = list.length;
    }
  };
  draw();
  $("#hideLow", el).addEventListener("change", draw);

  let dragId = null;
  el.addEventListener("dragstart", (e) => {
    const k = e.target.closest(".kcard");
    if (!k) return;
    dragId = k.dataset.id;
    k.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragId);
  });
  el.addEventListener("dragend", (e) => e.target.closest(".kcard")?.classList.remove("dragging"));
  $$(".col-body", el).forEach((body) => {
    body.addEventListener("dragover", (e) => { e.preventDefault(); body.classList.add("over"); });
    body.addEventListener("dragleave", () => body.classList.remove("over"));
    body.addEventListener("drop", async (e) => {
      e.preventDefault();
      body.classList.remove("over");
      const c = cases.find((x) => x.id === (e.dataTransfer.getData("text/plain") || dragId));
      const to = body.dataset.drop;
      if (!c || c.status === to) return;
      const from = c.status;
      c.status = to;                     // optimistic
      draw();
      try {
        await store.update(c, { status: to });
        toast(`${esc(c.number)} → ${STATUS_LABEL[to].toUpperCase()}`);
      } catch (err) {
        c.status = from;
        draw();
        toast(`Couldn't move ${esc(c.number)}: ${esc(err.message)}`);
      }
    });
  });
  el.addEventListener("click", (e) => {
    const k = e.target.closest(".kcard");
    if (k) { const c = cases.find((x) => x.id === k.dataset.id); location.hash = caseHref(c); }
  });
  el.addEventListener("keydown", (e) => {
    const k = e.target.closest(".kcard");
    if (k && e.key === "Enter") { const c = cases.find((x) => x.id === k.dataset.id); location.hash = caseHref(c); }
  });
}
