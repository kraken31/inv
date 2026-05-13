const state = {
  rows: [],
  filtered: [],
  sortKey: "name",
  sortDir: "asc",
  query: "",
  liquidite: null,
};

const tbody = document.querySelector("#wallet-table tbody");
const statusEl = document.getElementById("status");
const searchEl = document.getElementById("search");
const reloadEl = document.getElementById("reload");

const nfEur = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
});
const nfNum = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 4 });
const nfInt = new Intl.NumberFormat("fr-FR");
const nfPct = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function fmtPct(v) {
  return v == null || Number.isNaN(v) ? "" : `${nfPct.format(v)}\u00A0%`;
}

const nfRsi = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function fmtRsi(v) {
  return v == null || Number.isNaN(v) ? "" : nfRsi.format(v);
}

function rsiClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  if (v < 30) return "rsi-low";
  if (v > 70) return "rsi-high";
  return "";
}

function signClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}

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

function renderSummary() {
  let totalPurchase = 0;
  let totalCurrent = 0;
  let totalDividend = 0;
  let maxDate = null;

  for (const r of state.filtered) {
    totalPurchase += Number(r.purchase_amount) || 0;
    totalCurrent += Number(r.current_amount) || 0;
    totalDividend += Number(r.dividend) || 0;
    if (r.current_date && (!maxDate || r.current_date > maxDate)) {
      maxDate = r.current_date;
    }
  }

  const totalPlusMinus = totalCurrent - totalPurchase;
  const perf = totalPurchase > 0
    ? (100 * (totalCurrent - totalPurchase)) / totalPurchase
    : null;

  const set = (id, text, cls = "num") => {
    const el = document.getElementById(id);
    el.textContent = text;
    el.className = cls;
  };

  set("s-purchase", nfEur.format(totalPurchase));
  document.getElementById("s-date").textContent = formatCurrentDate(maxDate);
  set("s-current", nfEur.format(totalCurrent));
  set("s-dividend", nfEur.format(totalDividend));
  set(
    "s-liquidite",
    state.liquidite != null ? nfEur.format(state.liquidite) : "",
  );
  set("s-plus-minus", nfEur.format(totalPlusMinus), `num ${signClass(totalPlusMinus)}`);
  set("s-perf", fmtPct(perf), `num ${signClass(perf)}`);
}

function render() {
  tbody.innerHTML = "";
  renderSummary();
  for (const r of state.filtered) {
    const tr = document.createElement("tr");
    const per = r.per;
    if (per != null && !Number.isNaN(per)) {
      if (per >= 0 && per <= 10) tr.classList.add("row-good");
      else tr.classList.add("row-bad");
    }
    tr.innerHTML = `
      <td>${escapeHtml(r.name)}</td>
      <td class="num">${nfInt.format(r.quantity ?? 0)}</td>
      <td>${escapeHtml(r.purchase_date || "")}</td>
      <td class="num">${nfNum.format(r.purchase_price ?? 0)}</td>
      <td class="num">${nfEur.format(r.purchase_amount ?? 0)}</td>
      <td class="num">${nfEur.format(r.dividend ?? 0)}</td>
      <td>${escapeHtml(formatCurrentDate(r.current_date))}</td>
      <td class="num">${r.current_price != null ? nfNum.format(r.current_price) : ""}</td>
      <td class="num">${r.current_amount != null ? nfEur.format(r.current_amount) : ""}</td>
      <td class="num ${signClass(r.perf_div)}">${fmtPct(r.perf_div)}</td>
      <td class="num ${signClass(r.plus_minus_value)}">${r.plus_minus_value != null ? nfEur.format(r.plus_minus_value) : ""}</td>
      <td class="num ${signClass(r.perf)}">${fmtPct(r.perf)}</td>
      <td class="num">${r.per != null ? nfPct.format(r.per) : ""}</td>
      <td class="num ${rsiClass(r.rsi)}">${fmtRsi(r.rsi)}</td>
    `;
    tbody.appendChild(tr);
  }

  document.querySelectorAll("th.sort").forEach((th) => {
    th.classList.remove("asc", "desc");
    if (th.dataset.key === state.sortKey) th.classList.add(state.sortDir);
  });
}

function formatCurrentDate(s) {
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

async function loadData() {
  setStatus("Chargement…");
  try {
    const [walletResp, liquiditeResp] = await Promise.all([
      fetch("/api/wallet"),
      fetch("/api/liquidite"),
    ]);
    if (!walletResp.ok) {
      const err = await walletResp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${walletResp.status}`);
    }
    state.rows = await walletResp.json();
    if (liquiditeResp.ok) {
      const data = await liquiditeResp.json();
      state.liquidite = data.liquidite;
    } else {
      state.liquidite = null;
    }
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

loadData();
