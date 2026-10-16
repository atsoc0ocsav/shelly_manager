// Ask before submitting any form that carries data-confirm (CSP blocks inline handlers).
document.addEventListener("submit", (e) => {
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Live updates: watch the server's change counter and re-render the [data-live]
// regions of this page when it moves. Regions the user is typing in are left alone.
(() => {
  const body = document.body;
  const stateUrl = body.dataset.stateUrl;
  if (!stateUrl || !document.querySelector("[data-live]")) return;
  let version = Number(body.dataset.version);
  let timer;

  const schedule = (ms) => { clearTimeout(timer); timer = setTimeout(tick, ms); };

  const isEditing = (region) => {
    const a = document.activeElement;
    if (a && region.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
    return [...region.querySelectorAll("input[type=text], textarea")].some((i) => i.value);
  };

  async function refresh() {
    const r = await fetch(location.href, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), "text/html");
    document.querySelectorAll("[data-live]").forEach((region) => {
      const fresh = region.id && doc.getElementById(region.id);
      if (fresh && !isEditing(region)) region.replaceWith(document.adoptNode(fresh));
    });
  }

  async function tick() {
    if (document.hidden) return schedule(5000);
    try {
      const r = await fetch(stateUrl, { credentials: "same-origin", cache: "no-store",
                                        headers: { Accept: "application/json" } });
      if (r.ok && (r.headers.get("content-type") || "").includes("json")) {
        const s = await r.json();
        if (s.version !== version) { await refresh(); version = s.version; }
        return schedule(s.busy.length ? 1500 : 5000);
      }
    } catch (_) { /* network blip: back off and retry */ }
    schedule(15000);
  }

  document.addEventListener("visibilitychange", () => { if (!document.hidden) schedule(0); });
  schedule(1500);
})();
