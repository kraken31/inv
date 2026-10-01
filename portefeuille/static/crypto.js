/**
 * Page Crypto : liste du référentiel, recherche par nom ou ticker,
 * puis fiche (nom, ticker Yahoo, RSI). L'URL reflète `id`.
 */
const searchEl = document.getElementById("crypto-search");
const suggestEl = document.getElementById("crypto-suggestions");
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("crypto-detail");
const tableEl = document.getElementById("crypto-table");
const tbody = tableEl.querySelector("tbody");

const nfRsi = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const state = {
  rows: [],
  sortKey: "name",
  sortDir: "asc",
  selectedId: null,
};

function setStatus(msg, isError = false) {
  statusEl.textContent = msg || "";
  statusEl.classList.toggle("error", !!isError);
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

function formatDate(s) {
  if (!s) return "";
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
}

function formatRsi(v) {
  return v != null && !Number.isNaN(v) ? nfRsi.format(v) : "";
}

function rsiClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  if (v < 30) return "good";
  if (v > 70) return "bad";
  return "";
}

function rsiCellClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  if (v < 30) return "rsi-low";
  if (v > 70) return "rsi-high";
  return "";
}

function compare(a, b, key, dir) {
  const va = a[key];
  const vb = b[key];
  let cmp;
  if (typeof va === "number" && typeof vb === "number") {
    cmp = va - vb;
  } else if (va == null && vb == null) {
    cmp = 0;
  } else if (va == null) {
    cmp = 1;
  } else if (vb == null) {
    cmp = -1;
  } else {
    cmp = String(va).localeCompare(String(vb), "fr", {
      numeric: true,
      sensitivity: "base",
    });
  }
  return dir === "asc" ? cmp : -cmp;
}

function syncUrl(id) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("id", id);
  else url.searchParams.delete("id");
  window.history.replaceState(null, "", url);
}

let searchSeq = 0;
let searchTimer = null;

function hideSuggestions() {
  suggestEl.hidden = true;
  suggestEl.innerHTML = "";
}

function renderSuggestions(items) {
  if (!items.length) {
    hideSuggestions();
    return;
  }
  suggestEl.innerHTML = items
    .map(
      (it) => `
      <li data-id="${escapeHtml(it.id)}">
        <span class="suggest-name">${escapeHtml(it.name)}</span>
        <span class="suggest-id">${escapeHtml(it.ticker || it.id)}</span>
      </li>
    `,
    )
    .join("");
  suggestEl.hidden = false;
}

async function runSearch(q) {
  const seq = ++searchSeq;
  if (!q.trim()) {
    hideSuggestions();
    return;
  }
  try {
    const params = new URLSearchParams({ q });
    const resp = await fetch(`/api/crypto/search?${params}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const items = await resp.json();
    if (seq !== searchSeq) return;
    renderSuggestions(items);
  } catch (e) {
    if (seq !== searchSeq) return;
    setStatus(`Erreur: ${e.message}`, true);
  }
}

searchEl.addEventListener("input", (e) => {
  const q = e.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => runSearch(q), 150);
});

searchEl.addEventListener("focus", () => {
  if (searchEl.value.trim()) runSearch(searchEl.value);
});

suggestEl.addEventListener("mousedown", (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  e.preventDefault();
  selectCrypto(li.dataset.id);
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".autocomplete")) hideSuggestions();
});

function selectCrypto(id) {
  hideSuggestions();
  if (!id) return;
  state.selectedId = id;
  syncUrl(id);
  renderTable();
  loadDetail(id);
}

async function loadDetail(id) {
  setStatus("Chargement…");
  detailEl.hidden = true;
  try {
    const resp = await fetch(`/api/crypto/${encodeURIComponent(id)}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    setStatus("");
    renderDetail(data);
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
}

function renderDetail(data) {
  document.getElementById("crypto-name").textContent = data.name || "";
  document.getElementById("crypto-ticker").textContent = data.ticker || "";
  document.getElementById("crypto-ticker-kpi").textContent = data.ticker || "—";
  searchEl.value = data.name || "";

  const rsiEl = document.getElementById("crypto-rsi");
  rsiEl.textContent =
    data.rsi != null && !Number.isNaN(data.rsi) ? formatRsi(data.rsi) : "—";
  rsiEl.classList.remove("good", "bad");
  const rsiCls = rsiClass(data.rsi);
  if (rsiCls) rsiEl.classList.add(rsiCls);
  document.getElementById("crypto-rsi-date").textContent = data.rsi_date
    ? `au ${formatDate(data.rsi_date)}`
    : "";

  detailEl.hidden = false;
}

function renderTable() {
  tbody.innerHTML = "";
  if (!state.rows.length) {
    tableEl.hidden = true;
    return;
  }
  const rows = [...state.rows].sort((a, b) =>
    compare(a, b, state.sortKey, state.sortDir),
  );
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.classList.add("clickable");
    tr.dataset.id = r.id;
    if (r.id === state.selectedId) tr.classList.add("row-selected");
    tr.innerHTML = `
      <td>${escapeHtml(r.name || "")}</td>
      <td>${escapeHtml(r.ticker || "")}</td>
      <td class="num ${rsiCellClass(r.rsi)}">${formatRsi(r.rsi)}</td>
    `;
    tbody.appendChild(tr);
  }
  tableEl.hidden = false;
  document.querySelectorAll("#crypto-table th.sort").forEach((th) => {
    th.classList.remove("asc", "desc");
    if (th.dataset.key === state.sortKey) th.classList.add(state.sortDir);
  });
}

tbody.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  selectCrypto(tr.dataset.id);
});

document.querySelectorAll("#crypto-table th.sort").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = "asc";
    }
    renderTable();
  });
});

async function loadList() {
  setStatus("Chargement…");
  try {
    const resp = await fetch("/api/crypto/list");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    state.rows = await resp.json();
    setStatus("");
    renderTable();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
}

async function init() {
  const params = new URLSearchParams(window.location.search);
  const initialId = params.get("id");
  await loadList();
  if (initialId) selectCrypto(initialId);
}

init();
