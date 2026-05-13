const state = {
  rows: [],
  filtered: [],
  sortKey: "per",
  sortDir: "asc",
  query: "",
};

const tbody = document.querySelector("#per-table tbody");
const statusEl = document.getElementById("status");
const searchEl = document.getElementById("search");
const reloadEl = document.getElementById("reload");
const exportEl = document.getElementById("export-csv");

const nfPct = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function setStatus(msg, isError = false) {
  statusEl.textContent = msg || "";
  statusEl.classList.toggle("error", !!isError);
}

function compare(a, b, key, dir) {
  const va = a[key];
  const vb = b[key];
  let cmp;
  if (typeof va === "number" && typeof vb === "number") {
    cmp = va - vb;
  } else {
    cmp = String(va ?? "").localeCompare(String(vb ?? ""), "fr", {
      numeric: true,
      sensitivity: "base",
    });
  }
  return dir === "asc" ? cmp : -cmp;
}

function applyFilterSort() {
  const q = state.query.trim().toLowerCase();
  state.filtered = state.rows.filter((r) => {
    if (!q) return true;
    return (
      String(r.name || "").toLowerCase().includes(q) ||
      String(r.id || "").toLowerCase().includes(q)
    );
  });
  state.filtered.sort((a, b) => compare(a, b, state.sortKey, state.sortDir));
  render();
}

function formatDate(s) {
  if (!s) return "";
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function render() {
  tbody.innerHTML = "";
  for (const r of state.filtered) {
    const tr = document.createElement("tr");
    const per = r.per;
    if (per != null && !Number.isNaN(per)) {
      if (per >= 0 && per <= 10) tr.classList.add("row-good");
      else tr.classList.add("row-bad");
    }
    tr.innerHTML = `
      <td><a class="action-link" href="/action?id=${encodeURIComponent(r.id ?? "")}">${escapeHtml(r.name)}</a></td>
      <td>${escapeHtml(formatDate(r.date))}</td>
      <td class="num">${r.per != null ? nfPct.format(r.per) : ""}</td>
    `;
    tbody.appendChild(tr);
  }

  document.querySelectorAll("th.sort").forEach((th) => {
    th.classList.remove("asc", "desc");
    if (th.dataset.key === state.sortKey) th.classList.add(state.sortDir);
  });
}

async function loadData() {
  setStatus("Chargement…");
  try {
    const resp = await fetch("/api/per");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    state.rows = await resp.json();
    setStatus("");
    applyFilterSort();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
}

document.querySelectorAll("th.sort").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = "asc";
    }
    applyFilterSort();
  });
});

searchEl.addEventListener("input", (e) => {
  state.query = e.target.value;
  applyFilterSort();
});

reloadEl.addEventListener("click", loadData);

exportEl.addEventListener("click", () => {
  exportTableToCsv("#per-table", state.filtered, "per");
});

loadData();
