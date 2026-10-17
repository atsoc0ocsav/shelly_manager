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
    if (region.querySelector("dialog[open]")) return true; // never close an open dialog
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
    document.dispatchEvent(new Event("sm:refreshed")); // e.g. re-apply the table sort
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

// Sortable tables: click (or Enter/Space on) a header button to sort; click again to reverse.
// Default is IP ascending (the server's order). The choice survives live refreshes.
(() => {
  const DEFAULT = { key: "ip", dir: "asc" };
  let state = { ...DEFAULT };
  try { state = JSON.parse(sessionStorage.getItem("sm-sort")) || state; } catch (_) { /* keep default */ }

  const numeric = new Set(["gen", "update", "status"]);
  function apply(table) {
    if (!table) return;
    const tbody = table.tBodies[0];
    const rows = [...tbody.rows];
    const attr = "s" + state.key.charAt(0).toUpperCase() + state.key.slice(1);
    const val = (r) => (numeric.has(state.key) ? Number(r.dataset[attr] || 0) : r.dataset[attr] || "");
    const ip = (r) => r.dataset.sIp || "";
    rows.sort((a, b) => {
      const x = val(a), y = val(b);
      const c = x < y ? -1 : x > y ? 1 : 0;
      return (state.dir === "asc" ? c : -c) || (ip(a) < ip(b) ? -1 : 1); // tie-break on IP
    });
    rows.forEach((r) => tbody.appendChild(r));
    table.querySelectorAll("th").forEach((th) => {
      const k = th.querySelector("[data-sort-key]")?.dataset.sortKey;
      th.setAttribute("aria-sort", k === state.key ? (state.dir === "asc" ? "ascending" : "descending") : "none");
    });
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-sort-key]");
    if (!btn) return;
    const key = btn.dataset.sortKey;
    state = { key, dir: state.key === key && state.dir === "asc" ? "desc" : "asc" };
    sessionStorage.setItem("sm-sort", JSON.stringify(state));
    apply(btn.closest("table"));
  });
  const reapply = () => document.querySelectorAll("table.sortable").forEach(apply);
  document.addEventListener("sm:refreshed", reapply);
  reapply();
})();

// Update dialog: open/close, select all, live count, no submit with nothing selected.
(() => {
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-dialog]");
    if (opener) {
      const d = document.getElementById(opener.dataset.openDialog);
      if (d && !d.open) { d.showModal(); d.querySelector("input, button")?.focus(); }
    }
    if (e.target.closest("[data-close-dialog]")) e.target.closest("dialog")?.close();
  });
  const sync = (form) => {
    const boxes = [...form.querySelectorAll('input[name="hosts"]')];
    const n = boxes.filter((b) => b.checked).length;
    const all = form.querySelector("[data-select-all]");
    if (all) { all.checked = n === boxes.length; all.indeterminate = n > 0 && n < boxes.length; }
    const count = form.querySelector("[data-selected-count]");
    if (count) count.textContent = n;
    const submit = form.querySelector("[data-update-submit]");
    if (submit) submit.disabled = n === 0;
  };
  document.addEventListener("change", (e) => {
    const form = e.target.closest("[data-update-form]");
    if (!form) return;
    if (e.target.matches("[data-select-all]")) {
      form.querySelectorAll('input[name="hosts"]').forEach((b) => { b.checked = e.target.checked; });
    }
    sync(form);
  });
})();
