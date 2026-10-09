// Upload a drive recorded elsewhere (car dashcam, action cam): video + its GPS track.
import { CSRF } from "/js/core.js";

export default async function render(el) {
  el.innerHTML = `
    <div><a class="label" href="#/home">← Home</a><h1 class="u-h1">Upload a drive</h1></div>
    <form class="card form" id="f">
      <p class="label" style="margin:0">From a dashcam or another camera: the video plus the GPS track (GPX or CSV with time, lat, lon) it recorded.
        No GPS file? Use <a href="#/record">Record</a> instead: it captures both together.</p>
      <label class="field"><span>Video (MP4, MOV, WebM)</span><input class="input" type="file" name="video" accept="video/*" required></label>
      <label class="field"><span>GPS track (GPX / CSV)</span><input class="input" type="file" name="gps" accept=".gpx,.csv" required></label>
      <div class="row">
        <label class="field"><span>Camera</span><select class="select" name="camera">
          <option value="dashcam">Car dashcam</option><option value="phone">Phone on windscreen</option><option value="action">Helmet / action cam</option></select></label>
        <label class="field"><span>Name</span><input class="input" name="name" maxlength="40" placeholder="E.g. NH48 morning run"></label>
      </div>
      <div class="progress" id="bar" hidden><i></i></div>
      <p class="label" id="pct" hidden></p>
      <p class="err-box" id="err" hidden></p>
      <button class="btn primary block" id="go" type="submit"><i data-lucide="upload"></i>Upload &amp; earn</button>
    </form>`;
  const $ = (s) => el.querySelector(s);
  let xhr = null;
  $("#f").addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.set("source", "upload");
    xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/me/drives");
    Object.entries(CSRF).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    $("#go").disabled = true;
    $("#bar").hidden = $("#pct").hidden = false;
    $("#err").hidden = true;
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      $("#bar i").style.width = `${(ev.loaded / ev.total) * 100}%`;
      $("#pct").textContent = `Uploading ${(ev.loaded / 1e6).toFixed(0)} / ${(ev.total / 1e6).toFixed(0)} MB`;
    };
    xhr.onload = () => {
      const body = JSON.parse(xhr.responseText || "{}");
      if (xhr.status >= 300) return fail(typeof body.detail === "string" ? body.detail : "Upload failed");
      location.hash = `#/drives/${encodeURIComponent(body.id)}`;
    };
    xhr.onerror = () => fail("Upload failed: check your connection");
    xhr.send(fd);
  });
  const fail = (m) => { $("#err").textContent = m; $("#err").hidden = false; $("#go").disabled = false; $("#pct").hidden = true; };
  return () => xhr?.readyState < 4 && xhr.abort();
}
