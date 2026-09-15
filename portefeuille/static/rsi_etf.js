const state = {
  rows: [],
  filtered: [],
  sortKey: "rsi",
  sortDir: "asc",
  query: "",
};

const tbody = document.querySelector("#rsi-etf-table tbody");
const statusEl = document.getElementById("status");
const searchEl = document.getElementById("search");
const categoryEl = document.getElementById("etf-category-filter");
const peaEl = document.getElementById("etf-pea-filter");
const reloadEl = document.getElementById("reload");
const exportEl = document.getElementById("export-csv");

const nfTer = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const nfRsi = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
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
  } else if (va == null && vb == null) {
    cmp = 0;
  } else if (va == null) {
    cmp = 1;
  } else if (vb == null) {
    cmp = -1;
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

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function formatTer(v) {
  return v != null && !Number.isNaN(v) ? `${nfTer.format(v)}\u00A0%` : "";
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

function syncUrl({ category, pea } = {}) {
  const url = new URL(window.location.href);
  if (category) url.searchParams.set("category", category);
  else url.searchParams.delete("category");
  if (pea === "1" || pea === "0") url.searchParams.set("pea", pea);
  else url.searchParams.delete("pea");
  window.history.replaceState(null, "", url);
}

function render() {
  tbody.innerHTML = "";
  for (const r of state.filtered) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><a class="action-link" href="/etf?id=${encodeURIComponent(r.id ?? "")}">${escapeHtml(r.name)}</a></td>
      <td class="num">${formatTer(r.ter)}</td>
      <td class="num rsi-low">${r.rsi != null ? nfRsi.format(r.rsi) : ""}</td>
    `;
    tbody.appendChild(tr);
  }

  document.querySelectorAll("th.sort").forEach((th) => {
    th.classList.remove("asc", "desc");
    if (th.dataset.key === state.sortKey) th.classList.add(state.sortDir);
  });
}

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

async function loadData() {
  setStatus("Chargement…");
  try {
    const params = listQuery();
    const qs = params.toString();
    const resp = await fetch(qs ? `/api/rsi-etf?${qs}` : "/api/rsi-etf");
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

async function onFiltersChange() {
  syncUrl(currentFilters());
  await loadData();
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

categoryEl.addEventListener("change", onFiltersChange);
peaEl.addEventListener("change", onFiltersChange);

exportEl.addEventListener("click", () => {
  exportTableToCsv("#rsi-etf-table", state.filtered, "rsi-etf");
});

async function init() {
  const params = new URLSearchParams(window.location.search);
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
  await loadData();
}

init();
