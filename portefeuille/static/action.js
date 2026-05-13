/**
 * Page Action : champ de recherche avec autocomplete sur la table
 * `stocks` (nom ou ISIN), puis affichage de la synthèse de l'action
 * sélectionnée (PER, RSI, dividendes et résultats par année,
 * rendement n / n-1 / moyenne 5 ans / moyenne 10 ans).
 *
 * L'action courante est reflétée dans l'URL (`/action?id=<ISIN>`) pour
 * pouvoir bookmarker / recharger la page sur la même action.
 */
const searchEl = document.getElementById("action-search");
const suggestEl = document.getElementById("action-suggestions");
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("action-detail");

const nfNum = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

// Résultats nets : entiers € potentiellement très grands, on les
// affiche en notation compacte fr-FR ("40,6 M") comme dans la
// page Sécurité.
const nfCompact = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  compactDisplay: "short",
  maximumFractionDigits: 1,
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

function formatPct(v) {
  return v != null && !Number.isNaN(v) ? `${nfNum.format(v)}\u00A0%` : "";
}

function formatCompact(v) {
  return v != null && !Number.isNaN(v) ? nfCompact.format(v) : "";
}

function formatDate(s) {
  if (!s) return "";
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
}

// ---------- Autocomplete ----------

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
      `/api/action/search?q=${encodeURIComponent(q)}`,
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const items = await resp.json();
    // Ignore les réponses obsolètes (l'utilisateur a continué à taper).
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
  // mousedown plutôt que click pour éviter le blur du champ.
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  e.preventDefault();
  selectAction(li.dataset.id);
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".autocomplete")) hideSuggestions();
});

// ---------- Rendu détail ----------

function selectAction(id) {
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
    const resp = await fetch(`/api/action/${encodeURIComponent(id)}`);
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

function setText(elId, value) {
  document.getElementById(elId).textContent = value;
}

function perClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  return v > 0 && v < 10 ? "good" : "bad";
}

function rsiClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  if (v < 30) return "good";
  if (v > 70) return "bad";
  return "";
}

function setKpi(elId, value, cls) {
  const el = document.getElementById(elId);
  el.textContent = value;
  el.classList.remove("good", "bad");
  if (cls) el.classList.add(cls);
}

function renderDetail(data) {
  setText("action-name", data.name || "");
  setText("action-id", data.id || "");
  searchEl.value = data.name || "";

  setKpi(
    "action-per",
    data.per != null ? formatNum(data.per) : "—",
    perClass(data.per),
  );
  setKpi(
    "action-rsi",
    data.rsi != null ? formatNum(data.rsi) : "—",
    rsiClass(data.rsi),
  );
  setText(
    "action-per-date",
    data.per_date ? `au ${formatDate(data.per_date)}` : "",
  );
  setText(
    "action-rsi-date",
    data.rsi_date ? `au ${formatDate(data.rsi_date)}` : "",
  );

  setText("r-year-n", data.year_n ?? "");
  setText("r-year-n1", data.year_n1 ?? "");
  setText("r-n", formatPct(data.rendement_n));
  setText("r-n1", formatPct(data.rendement_n1));
  setText("r-avg5", formatPct(data.rendement_avg5));
  setText("r-avg10", formatPct(data.rendement_avg10));

  const divBody = document.querySelector("#dividends-table tbody");
  divBody.innerHTML = "";
  if (!data.dividends || !data.dividends.length) {
    divBody.innerHTML =
      '<tr><td colspan="2" class="empty">Aucun dividende connu</td></tr>';
  } else {
    for (const d of data.dividends) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(d.year)}</td>
        <td class="num">${formatNum(d.dividend)}</td>
      `;
      divBody.appendChild(tr);
    }
  }

  const resBody = document.querySelector("#results-table tbody");
  resBody.innerHTML = "";
  if (!data.results || !data.results.length) {
    resBody.innerHTML =
      '<tr><td colspan="2" class="empty">Aucun résultat connu</td></tr>';
  } else {
    for (const r of data.results) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(r.year)}</td>
        <td class="num">${formatCompact(r.result)}</td>
      `;
      resBody.appendChild(tr);
    }
  }

  detailEl.hidden = false;
}

// Si on arrive sur /action?id=..., on charge directement la synthèse.
const initialId = new URLSearchParams(window.location.search).get("id");
if (initialId) loadDetail(initialId);
