/**
 * Page ETF : champ de recherche avec autocomplete sur la table `etf`
 * (nom ou ticker), puis affichage du nom, du ticker et du dernier
 * cours connu (`pricing_etf`).
 *
 * L'ETF courant est reflété dans l'URL (`/etf?id=<ticker>`) pour
 * pouvoir bookmarker / recharger la page sur le même titre.
 */
const searchEl = document.getElementById("etf-search");
const suggestEl = document.getElementById("etf-suggestions");
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("etf-detail");

const nfNum = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

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

function formatDate(s) {
  if (!s) return "";
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
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

async function runSearch(q) {
  const seq = ++searchSeq;
  if (!q.trim()) {
    hideSuggestions();
    return;
  }
  try {
    const resp = await fetch(
      `/api/etf/search?q=${encodeURIComponent(q)}`,
    );
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
  const url = new URL(window.location.href);
  url.searchParams.set("id", id);
  window.history.replaceState(null, "", url);
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

  detailEl.hidden = false;
}

const initialId = new URLSearchParams(window.location.search).get("id");
if (initialId) loadDetail(initialId);
