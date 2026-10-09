// Account: profile, change password, sign out.
import { api, currentUser, esc, logout, toast } from "/js/core.js";

export default async function render(el) {
  const u = currentUser();
  el.innerHTML = `
    <h1 class="u-h1">Account</h1>
    <section class="card">
      <dl class="facts"><dt>Name</dt><dd>${esc(u.name)}</dd><dt>Email</dt><dd>${esc(u.email)}</dd>
        <dt>Member since</dt><dd>${new Date(u.created_at).toLocaleDateString([], { dateStyle: "medium" })}</dd></dl>
    </section>
    <form class="card form" id="pw">
      <div class="section-head" style="margin:0"><h2>Change password</h2></div>
      <label class="field"><span>Current password</span><input class="input" type="password" name="current" autocomplete="current-password" required></label>
      <label class="field"><span>New password</span><input class="input" type="password" name="new" autocomplete="new-password" minlength="8" required></label>
      <p class="err-box" id="err" hidden></p>
      <button class="btn" type="submit">Update password</button>
    </form>
    <a class="btn block" href="#/drives"><i data-lucide="route"></i>My drives</a>
    ${u.role === "admin" ? `<a class="btn block" href="/">Open the admin dashboard</a>` : ""}
    <button class="btn block" id="out"><i data-lucide="log-out"></i>Sign out</button>`;
  el.querySelector("#out").addEventListener("click", logout);
  el.querySelector("#pw").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = el.querySelector("#err");
    err.hidden = true;
    try {
      await api("/api/auth/password", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ current: f.current.value, new: f.new.value }) });
      f.reset();
      toast("Password updated. Other devices were signed out.");
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    }
  });
}
