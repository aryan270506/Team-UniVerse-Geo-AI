// New drive (upload) and Go live (phone / replay) dialogs.
import { $, $$, api, icons } from "./core.js";

export function openUpload() {
  $("#uploadError").hidden = true;
  $("#uploadProgress").hidden = true;
  $("#uploadSubmit").disabled = false;
  $("#uploadDlg").showModal();
}

export function openLive(tab = "phone") {
  showTab(tab);
  $("#replayError").hidden = true;
  $("#liveDlg").showModal();
}

function showTab(tab) {
  $$("#liveDlg .dlg-tabs button").forEach((b) => { b.classList.toggle("on", b.dataset.tab === tab); b.setAttribute("aria-selected", b.dataset.tab === tab); });
  $$("#liveDlg [data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== tab; });
}

export function initDialogs() {
  $("#newDriveBtn").addEventListener("click", openUpload);
  $("#goLiveBtn").addEventListener("click", () => openLive());
  $$("dialog").forEach((d) => d.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]") || e.target === d) d.close();
  }));
  $$("#liveDlg .dlg-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

  // ---- upload: XHR for progress, then watch it in Command
  $("#uploadForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target, fd = new FormData(f);
    fd.set("use_world", f.use_world.checked ? "true" : "false");
    fd.set("auto_calibrate", f.auto_calibrate.checked ? "true" : "false");
    $("#uploadSubmit").disabled = true;
    $("#uploadProgress").hidden = false;
    $("#uploadError").hidden = true;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/process");
    xhr.setRequestHeader("X-TerraTrace", "1");
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      $("#uploadBar").style.width = `${(ev.loaded / ev.total) * 100}%`;
      $("#uploadText").textContent = `Uploading ${(ev.loaded / 1e6).toFixed(0)} / ${(ev.total / 1e6).toFixed(0)} MB`;
    };
    xhr.onload = () => {
      if (xhr.status >= 300) return fail((JSON.parse(xhr.responseText || "{}").detail) || xhr.statusText);
      const info = JSON.parse(xhr.responseText);
      $("#uploadDlg").close();
      f.reset();
      location.hash = `#/command/live/${encodeURIComponent(info.id)}`;
    };
    xhr.onerror = () => fail("Upload failed");
    xhr.send(fd);
  });
  const fail = (msg) => { $("#uploadError").textContent = msg; $("#uploadError").hidden = false; $("#uploadSubmit").disabled = false; };

  // ---- replay
  $("#replayForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#replaySubmit").disabled = true;
    try {
      const info = await api("/api/live/replay", { method: "POST", body: new FormData(e.target) });
      $("#liveDlg").close();
      location.hash = `#/command/live/${encodeURIComponent(info.id)}`;
    } catch (err) {
      $("#replayError").textContent = err.message;
      $("#replayError").hidden = false;
    } finally {
      $("#replaySubmit").disabled = false;
    }
  });

  // ---- phone session + QR
  $("#phoneCreate").addEventListener("click", async () => {
    const fd = new FormData();
    fd.set("name", "phone drive");
    const info = await api("/api/live/device", { method: "POST", body: fd });
    const url = info.phone_urls[0];
    $("#phoneUrl").href = url;
    $("#phoneUrl").textContent = url;
    $("#phoneWarn").hidden = info.secure;
    if (window.qrcode) {
      const qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      $("#phoneQr").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
    }
    $("#phoneSetup").hidden = true;
    $("#phoneLink").hidden = false;
    location.hash = `#/command/live/${encodeURIComponent(info.id)}`;
  });
  $("#liveDlg").addEventListener("close", () => { $("#phoneSetup").hidden = false; $("#phoneLink").hidden = true; });
  icons();
}
