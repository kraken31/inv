/**
 * Page ETF : recherche par nom/ticker limitée aux filtres
 * (catégorie + PEA ; Tous = tout le référentiel), tableau filtré,
 * puis fiche (prix, TER, catégorie, PEA). L'URL reflète `id`,
 * éventuellement `category` et `pea`.
 */
const searchEl = document.getElementById("etf-search");
const suggestEl = document.getElementById("etf-suggestions");
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("etf-detail");
const categoryEl = document.getElementById("etf-category-filter");
const peaEl = document.getElementById("etf-pea-filter");
const tableEl = document.getElementById("etf-table");
const tbody = tableEl.querySelector("tbody");

const nfNum = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const nfTer = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
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

function formatNum(v) {
  return v != null && !Number.isNaN(v) ? nfNum.format(v) : "";
}

function formatTer(v) {
  return v != null && !Number.isNaN(v) ? `${nfTer.format(v)}\u00A0%` : "";
}

function formatDate(s) {
  if (!s) return "";
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
}

function formatRsi(v) {
  return v != null && !Number.isNaN(v) ? nfRsi.format(v) : "";
}

function formatPea(v) {
  if (v === true) return "Oui";
  if (v === false) return "Non";
  return "—";
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

function parseDateLike(s) {
  if (s == null) return null;
  const str = String(s);
  let m = str.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m) return Date.UTC(+m[3], +m[2] - 1, +m[1]);
  m = str.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3]);
  return null;
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
    const da = parseDateLike(va);
    const db = parseDateLike(vb);
    if (da != null && db != null) {
      cmp = da - db;
    } else {
      cmp = String(va).localeCompare(String(vb), "fr", {
        numeric: true,
        sensitivity: "base",
      });
    }
  }
  return dir === "asc" ? cmp : -cmp;
}

function syncUrl({ id, category, pea } = {}) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("id", id);
  else url.searchParams.delete("id");
  if (category) url.searchParams.set("category", category);
  else url.searchParams.delete("category");
  if (pea === "1" || pea === "0") url.searchParams.set("pea", pea);
  else url.searchParams.delete("pea");
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
        <span class="suggest-id">${escapeHtml(it.id)}</span>
      </li>
    `,
    )
    .join("");
  suggestEl.hidden = false;
}

function selectedCategory() {
  return categoryEl.value || "";
}

function selectedPea() {
  const v = peaEl.value;
  return v === "1" || v === "0" ? v : "";
}

function currentFilters() {
  return {
    category: selectedCategory() || null,
    pea: selectedPea() || null,
  };
}

function listQuery() {
  const params = new URLSearchParams();
  const category = selectedCategory();
  const pea = selectedPea();
  if (category) params.set("category", category);
  if (pea) params.set("pea", pea);
  return params;
}

async function runSearch(q) {
  const seq = ++searchSeq;
  if (!q.trim()) {
    hideSuggestions();
    return;
  }
  try {
    const params = new URLSearchParams({ q });
    const category = selectedCategory();
    const pea = selectedPea();
    if (category) params.set("category", category);
    if (pea) params.set("pea", pea);
    const resp = await fetch(`/api/etf/search?${params}`);
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
  selectEtf(li.dataset.id);
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".autocomplete")) hideSuggestions();
});

function selectEtf(id) {
  hideSuggestions();
  if (!id) return;
  state.selectedId = id;
  syncUrl({ id, ...currentFilters() });
  renderTable();
  loadDetail(id);
}

async function loadDetail(id) {
  setStatus("Chargement…");
  detailEl.hidden = true;
  try {
    const resp = await fetch(`/api/etf/${encodeURIComponent(id)}`);
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
  document.getElementById("etf-name").textContent = data.name || "";
  document.getElementById("etf-id").textContent = data.id || "";
  searchEl.value = data.name || "";

  const priceEl = document.getElementById("etf-price");
  priceEl.textContent =
    data.price != null && !Number.isNaN(data.price)
      ? formatNum(data.price)
      : "—";
  document.getElementById("etf-price-date").textContent = data.price_date
    ? `au ${formatDate(data.price_date)}`
    : "aucun cours connu";

  const terEl = document.getElementById("etf-ter");
  terEl.textContent =
    data.ter != null && !Number.isNaN(data.ter) ? formatTer(data.ter) : "—";

  const catEl = document.getElementById("etf-category");
  catEl.textContent = data.category ? data.category : "—";

  const peaEl = document.getElementById("etf-pea");
  peaEl.textContent = formatPea(data.pea);
  peaEl.classList.remove("good", "bad");
  if (data.pea === true) peaEl.classList.add("good");

  const rsiEl = document.getElementById("etf-rsi");
  rsiEl.textContent =
    data.rsi != null && !Number.isNaN(data.rsi) ? formatRsi(data.rsi) : "—";
  rsiEl.classList.remove("good", "bad");
  const rsiCls = rsiClass(data.rsi);
  if (rsiCls) rsiEl.classList.add(rsiCls);
  document.getElementById("etf-rsi-date").textContent = data.rsi_date
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
      <td>${escapeHtml(r.id || "")}</td>
      <td class="num">${r.ter != null ? formatTer(r.ter) : ""}</td>
      <td class="num">${r.price != null ? formatNum(r.price) : ""}</td>
      <td class="num ${rsiCellClass(r.rsi)}">${formatRsi(r.rsi)}</td>
      <td>${escapeHtml(formatDate(r.price_date))}</td>
    `;
    tbody.appendChild(tr);
  }
  tableEl.hidden = false;
  document.querySelectorAll("#etf-table th.sort").forEach((th) => {
    th.classList.remove("asc", "desc");
    if (th.dataset.key === state.sortKey) th.classList.add(state.sortDir);
  });
}

tbody.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  selectEtf(tr.dataset.id);
});

document.querySelectorAll("#etf-table th.sort").forEach((th) => {
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

async function loadCategories() {
  const resp = await fetch("/api/etf/categories");
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  const cats = await resp.json();
  const current = categoryEl.value;
  const opts = ['<option value="">Toutes les classes</option>'];
  for (const cat of cats) {
    opts.push(
      `<option value="${escapeHtml(cat)}">${escapeHtml(cat)}</option>`,
    );
  }
  categoryEl.innerHTML = opts.join("");
  if (current && cats.includes(current)) categoryEl.value = current;
}

async function loadFilteredList() {
  setStatus("Chargement…");
  try {
    const params = listQuery();
    const qs = params.toString();
    const resp = await fetch(qs ? `/api/etf/list?${qs}` : "/api/etf/list");
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

async function onFiltersChange() {
  hideSuggestions();
  await loadFilteredList();
  if (state.selectedId) {
    const stillThere = state.rows.some((r) => r.id === state.selectedId);
    if (!stillThere) {
      state.selectedId = null;
      detailEl.hidden = true;
      searchEl.value = "";
    }
  }
  syncUrl({ id: state.selectedId, ...currentFilters() });
  renderTable();
  if (searchEl.value.trim()) runSearch(searchEl.value);
}

categoryEl.addEventListener("change", onFiltersChange);
peaEl.addEventListener("change", onFiltersChange);

async function init() {
  const params = new URLSearchParams(window.location.search);
  const initialId = params.get("id");
  const initialCategory = params.get("category") || "";
  const initialPea = params.get("pea") || "";
  try {
    await loadCategories();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
    return;
  }
  if (initialCategory) categoryEl.value = initialCategory;
  if (initialPea === "1" || initialPea === "0") peaEl.value = initialPea;
  await loadFilteredList();
  if (initialId) selectEtf(initialId);
}

init();
