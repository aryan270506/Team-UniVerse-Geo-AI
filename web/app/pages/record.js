// In-app dashcam: records video (MediaRecorder) and GPS (watchPosition) together, then uploads
// both as one drive. The recorder's start time is sent so the server syncs video and GPS exactly.
import { CSRF, esc } from "/js/core.js";

// The recording stays in phone memory until upload, so budget phones get a lower bitrate and a
// shorter cap (navigator.deviceMemory: Chrome/Android, in GB; other browsers assume mid-range).
const MEM_GB = navigator.deviceMemory || 4;
const LOW_MEM = MEM_GB <= 2;
const MAX_S = (LOW_MEM ? 15 : 30) * 60;      // 30 min reaches the per-drive coin cap
const BITRATE = LOW_MEM ? 1_500_000 : MEM_GB <= 4 ? 2_000_000 : 2_500_000;
const COIN_S = 30, DRIVE_CAP = 60, MAX_ACC = 50;
const MIMES = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];

const km = (a, b) => {
  const R = 6371, r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
};
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default async function render(el) {
  el.innerHTML = `
    <div class="rec-screen" id="rec">
      <video id="cam" playsinline muted autoplay></video>
      <div class="rec-top">
        <span class="rec-pill"><i class="dot" id="recDot"></i><span id="recState">Camera off</span></span>
        <span class="rec-pill"><i class="dot" id="gpsDot"></i><span id="gpsText">GPS –</span></span>
        <span class="rec-pill"><span id="dist">0.0 km</span></span>
      </div>
      <a class="rec-close" href="#/home" aria-label="Close recorder"><i data-lucide="x"></i></a>
      <div class="rec-hint" id="hint">Mount the phone on the windscreen in landscape, horizon near the middle. Then tap the red button.</div>
      <div class="rec-bottom" id="controls">
        <div class="side"><b id="time">0:00</b>recording</div>
        <button class="big" id="go" aria-label="Start recording" disabled></button>
        <div class="side r"><b id="coins">0</b>coins so far</div>
      </div>
    </div>`;

  const $ = (s) => el.querySelector(s);
  let stream = null, rec = null, chunks = [], mime = "", startEpoch = 0, stopEpoch = 0;
  let watchId = null, wake = null, tick = null, fixes = [], lastGood = null, dist = 0, unsaved = false;

  const hint = (t) => { $("#hint").hidden = !t; $("#hint").innerHTML = t || ""; };
  const elapsed = () => ((stopEpoch || Date.now()) - startEpoch) / 1000;

  function onFix(p) {
    const { latitude: lat, longitude: lon, accuracy: acc } = p.coords;
    const f = { t: p.timestamp || Date.now(), lat, lon, acc };
    const ok = acc <= MAX_ACC;
    $("#gpsDot").className = `dot ${acc <= 15 ? "ok" : ok ? "warn" : "bad"}`;
    $("#gpsText").textContent = ok ? `GPS ±${Math.round(acc)} m` : `GPS weak ±${Math.round(acc)} m`;
    if (!rec) { fixes = [f]; return; }      // before recording: keep just the latest fix
    fixes.push(f);
    if (ok) {
      if (lastGood) dist += km(lastGood, f);
      lastGood = f;
      $("#dist").textContent = `${dist.toFixed(1)} km`;
    }
  }

  async function openCamera() {
    if (!window.isSecureContext) throw new Error("Recording needs the secure (https://) address.");
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error("This browser can't record video. Use Chrome or Safari.");
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } }, audio: false });
    $("#cam").srcObject = stream;
    watchId = navigator.geolocation.watchPosition(onFix, (e) => {
      $("#gpsDot").className = "dot bad";
      $("#gpsText").textContent = e.code === 1 ? "GPS blocked" : "GPS: no fix";
      if (e.code === 1) hint("Location is blocked. Allow location for this site, or the drive can't be mapped and won't earn coins.");
    }, { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 });
    $("#recState").textContent = "Ready";
    if (LOW_MEM) hint(`Low-memory phone: recordings stop automatically after ${MAX_S / 60} min. Upload, then start a new one.`);
    $("#go").disabled = false;
  }

  function start() {
    mime = MIMES.find((m) => MediaRecorder.isTypeSupported(m)) || "";
    rec = new MediaRecorder(stream, { ...(mime ? { mimeType: mime } : {}), videoBitsPerSecond: BITRATE });
    chunks = [];
    fixes = fixes.filter((f) => Date.now() - f.t < 10000);           // the warm-up fix brackets the start
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstart = () => { startEpoch = Date.now(); };
    rec.onstop = review;
    rec.start(1000);
    startEpoch = Date.now();
    unsaved = true;
    navigator.wakeLock?.request("screen").then((w) => { wake = w; }).catch(() => {});
    $("#go").classList.add("on");
    $("#go").setAttribute("aria-label", "Stop recording");
    $("#recDot").className = "dot live";
    $("#recState").textContent = "Recording";
    hint("");
    tick = setInterval(() => {
      const s = elapsed();
      $("#time").textContent = clock(s);
      $("#coins").textContent = Math.min(DRIVE_CAP, Math.floor(s / COIN_S));
      if (s >= MAX_S) stop();
    }, 500);
  }

  function stop() {
    if (!rec || rec.state === "inactive") return;
    stopEpoch = Date.now();
    clearInterval(tick);
    rec.stop();
    $("#recDot").className = "dot";
    $("#recState").textContent = "Stopped";
  }

  function review() {
    const blob = new Blob(chunks, { type: mime || chunks[0]?.type || "video/webm" });
    const secs = elapsed();
    const good = fixes.filter((f) => f.acc <= MAX_ACC).length;
    const est = secs < COIN_S ? 0 : Math.min(DRIVE_CAP, Math.floor(secs / COIN_S));
    wake?.release?.();
    hint("");
    $("#controls").outerHTML = `<form class="rec-sheet" id="sheet">
      <div><b style="font-size:var(--fs-lg)">Recorded ${clock(secs)}</b>
        <div class="label">${dist.toFixed(1)} km · ${good} GPS points · ${(blob.size / 1e6).toFixed(0)} MB · up to ≈${est} coins + hazard bonus</div></div>
      ${good < 2 ? `<p class="err-box">No usable GPS was recorded, so this drive can't be mapped or rewarded.</p>` : ""}
      <label class="field"><span>Name this drive</span><input class="input" name="name" maxlength="40" placeholder="E.g. Office commute"></label>
      <div class="progress" id="bar" hidden><i></i></div>
      <p class="err-box" id="upErr" hidden></p>
      <div style="display:grid;grid-template-columns:1fr 2fr;gap:10px">
        <button type="button" class="btn" id="discard">Discard</button>
        <button type="submit" class="btn primary" id="send" ${good < 2 ? "disabled" : ""}><i data-lucide="upload"></i>Upload &amp; earn</button>
      </div></form>`;
    window.lucide?.createIcons({ root: el });
    $("#discard").onclick = () => { if (confirm("Delete this recording?")) { unsaved = false; location.hash = "#/home"; } };
    $("#sheet").onsubmit = (e) => { e.preventDefault(); upload(blob, secs, e.target.name.value); };
  }

  function upload(blob, secs, name) {
    const fd = new FormData();
    fd.set("video", blob, `recording.${blob.type.includes("mp4") ? "mp4" : "webm"}`);
    fd.set("gps_fixes", JSON.stringify(fixes));
    fd.set("source", "record");
    fd.set("camera", "phone");
    fd.set("video_start", String(startEpoch));
    fd.set("duration_s", String(secs));
    fd.set("name", name.trim());
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/me/drives");
    Object.entries(CSRF).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    $("#bar").hidden = false;
    $("#send").disabled = $("#discard").disabled = true;
    $("#upErr").hidden = true;
    xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) $("#bar i").style.width = `${(ev.loaded / ev.total) * 100}%`; };
    xhr.onload = () => {
      const body = JSON.parse(xhr.responseText || "{}");
      if (xhr.status >= 300) return fail(typeof body.detail === "string" ? body.detail : "Upload failed");
      unsaved = false;
      location.hash = `#/drives/${encodeURIComponent(body.id)}`;
    };
    xhr.onerror = () => fail("Upload failed: check your connection and try again");
    xhr.send(fd);
  }
  const fail = (msg) => {
    $("#upErr").textContent = msg;
    $("#upErr").hidden = false;
    $("#send").disabled = $("#discard").disabled = false;
  };

  $("#go").addEventListener("click", () => (rec && rec.state === "recording" ? stop() : start()));
  $(".rec-close").addEventListener("click", (e) => {
    if (unsaved && !confirm("Leave without uploading? This recording will be lost.")) e.preventDefault();
    else unsaved = false;
  });
  const guard = (e) => { if (unsaved) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", guard);
  try { await openCamera(); } catch (e) {
    hint(esc(e.name === "NotAllowedError" ? "Camera permission was denied. Allow camera access for this site, then reopen Record." : e.message));
  }

  return () => {
    if (rec && rec.state !== "inactive") { rec.onstop = null; rec.stop(); }
    clearInterval(tick);
    stream?.getTracks().forEach((t) => t.stop());
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    wake?.release?.();
    window.removeEventListener("beforeunload", guard);
  };
}
