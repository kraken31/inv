const state = {
  rows: [],
  filtered: [],
  sortKey: "rendement",
  sortDir: "desc",
  query: "",
};

const tbody = document.querySelector("#rendement-table tbody");
const statusEl = document.getElementById("status");
const searchEl = document.getElementById("search");
const reloadEl = document.getElementById("reload");
const exportEl = document.getElementById("export-csv");

const nfNum = new Intl.NumberFormat("fr-FR", {
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
  const aNull = va == null || Number.isNaN(va);
  const bNull = vb == null || Number.isNaN(vb);
  if (aNull && bNull) return 0;
  if (aNull) return 1;
  if (bNull) return -1;
  let cmp;
  if (typeof va === "number" && typeof vb === "number") {
    cmp = va - vb;
  } else {
    cmp = String(va).localeCompare(String(vb), "fr", {
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

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function formatNum(v) {
  return v != null && !Number.isNaN(v) ? nfNum.format(v) : "";
}

function formatPct(v) {
  return v != null && !Number.isNaN(v) ? `${nfNum.format(v)}\u00A0%` : "";
}

function render() {
  tbody.innerHTML = "";
  for (const r of state.filtered) {
    const tr = document.createElement("tr");
    const per = r.per;
    if (per != null && !Number.isNaN(per)) {
      if (per > 0 && per < 10) tr.classList.add("row-good");
      else if (per >= 10 || per <= 0) tr.classList.add("row-bad");
    }
    tr.innerHTML = `
      <td><a class="action-link" href="/action?id=${encodeURIComponent(r.id ?? "")}">${escapeHtml(r.name)}</a></td>
      <td class="num">${formatNum(r.per)}</td>
      <td class="num">${formatNum(r.dividend)}</td>
      <td class="num">${formatPct(r.rendement)}</td>
      <td class="num">${formatNum(r.dividend_prev)}</td>
      <td class="num">${formatPct(r.rendement_prev)}</td>
      <td class="num">${formatNum(r.dividend_avg5)}</td>
      <td class="num">${formatPct(r.rendement_avg5)}</td>
      <td class="num">${formatNum(r.dividend_avg10)}</td>
      <td class="num">${formatPct(r.rendement_avg10)}</td>
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
    const resp = await fetch("/api/rendement");
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
      state.sortDir = key === "name" ? "asc" : "desc";
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
  exportTableToCsv("#rendement-table", state.filtered, "rendement");
});

loadData();
