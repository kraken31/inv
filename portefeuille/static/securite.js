const state = {
  rows: [],
  filtered: [],
  sortKey: "per",
  sortDir: "asc",
  query: "",
};

const tbody = document.querySelector("#securite-table tbody");
const statusEl = document.getElementById("status");
const searchEl = document.getElementById("search");
const reloadEl = document.getElementById("reload");
const exportEl = document.getElementById("export-csv");

const nfPer = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

// Les résultats nets viennent en € bruts (entiers signés). On les
// affiche en notation compacte fr-FR ("40,6 M") pour rester lisible
// quel que soit l'ordre de grandeur, du million au milliard.
const nfResult = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  compactDisplay: "short",
  maximumFractionDigits: 1,
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

function formatResult(v) {
  return v != null && !Number.isNaN(v) ? nfResult.format(v) : "";
}

function formatPer(v) {
  return v != null && !Number.isNaN(v) ? nfPer.format(v) : "";
}

function growthClass(r) {
  // Coloration suivant le nombre d'années *consécutives* de
  // croissance stricte, en partant de la plus récente :
  //   - 4 ans (n-3 < n-2 < n-1 < n) -> vert  (row-good)
  //   - 3 ans (n-2 < n-1 < n)       -> jaune (row-warn)
  //   - 2 ans (n-1 < n)             -> orange (row-mid)
  //   - sinon                        -> rouge (row-bad)
  const a = r.result_n3;
  const b = r.result_n2;
  const c = r.result_n1;
  const d = r.result_n;
  if (a != null && b != null && c != null && d != null
      && a < b && b < c && c < d) return "row-good";
  if (b != null && c != null && d != null && b < c && c < d) return "row-warn";
  if (c != null && d != null && c < d) return "row-mid";
  return "row-bad";
}

function render() {
  tbody.innerHTML = "";
  for (const r of state.filtered) {
    const tr = document.createElement("tr");
    tr.classList.add(growthClass(r));
    tr.innerHTML = `
      <td><a class="action-link" href="/action?id=${encodeURIComponent(r.id ?? "")}">${escapeHtml(r.name)}</a></td>
      <td class="num">${formatResult(r.result_n3)}</td>
      <td class="num">${formatResult(r.result_n2)}</td>
      <td class="num">${formatResult(r.result_n1)}</td>
      <td class="num">${formatResult(r.result_n)}</td>
      <td class="num">${formatPer(r.per)}</td>
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
    const resp = await fetch("/api/securite");
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
  exportTableToCsv("#securite-table", state.filtered, "securite");
});

loadData();
