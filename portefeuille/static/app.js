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
  } else {
    const da = parseDateLike(va);
    const db = parseDateLike(vb);
    if (da != null && db != null) {
      cmp = da - db;
    } else if (da != null) {
      cmp = -1;
    } else if (db != null) {
      cmp = 1;
    } else {
      cmp = String(va ?? "").localeCompare(String(vb ?? ""), "fr", {
        numeric: true,
        sensitivity: "base",
      });
    }
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

  const liquiditeEl = document.getElementById("s-liquidite");
  const amountText = state.liquidite != null ? nfEur.format(state.liquidite) : "";
  liquiditeEl.className = "num";
  liquiditeEl.innerHTML = `
    <span class="cell-value">${escapeHtml(amountText)}</span>
    <button type="button" class="cell-edit" title="Modifier la liquidité"
            aria-label="Modifier la liquidité">✎</button>
  `;

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
      <td class="col-actions">
        <button type="button" class="row-edit"
                data-id="${escapeHtml(r.id ?? "")}"
                data-name="${escapeHtml(r.name ?? "")}"
                data-quantity="${r.quantity ?? 0}"
                data-date="${escapeHtml(r.purchase_date ?? "")}"
                data-price="${r.purchase_price ?? 0}"
                data-dividend="${r.dividend ?? 0}"
                title="Modifier cette ligne"
                aria-label="Modifier">✎</button>
        <button type="button" class="row-delete"
                data-id="${escapeHtml(r.id ?? "")}"
                data-name="${escapeHtml(r.name ?? "")}"
                title="Supprimer cette ligne"
                aria-label="Supprimer">🗑</button>
      </td>
      <td><a class="action-link" href="/action?id=${encodeURIComponent(r.id ?? "")}">${escapeHtml(r.name)}</a></td>
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

function toIsoDate(s) {
  if (!s) return "";
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  const m2 = String(s).match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m2) return `${m2[3]}-${m2[2]}-${m2[1]}`;
  return "";
}

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
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

const liquiditeDialog = document.getElementById("liquidite-dialog");
const liquiditeInput = document.getElementById("liquidite-input");

document.getElementById("s-liquidite").addEventListener("click", (e) => {
  if (!e.target.closest(".cell-edit")) return;
  liquiditeInput.value = state.liquidite != null ? state.liquidite : "";
  if (typeof liquiditeDialog.showModal === "function") {
    liquiditeDialog.showModal();
    liquiditeInput.focus();
    liquiditeInput.select();
  }
});

liquiditeDialog.addEventListener("close", async () => {
  if (liquiditeDialog.returnValue !== "save") return;
  const value = Number(liquiditeInput.value);
  if (!Number.isFinite(value) || value < 0) {
    setStatus("Liquidité invalide", true);
    return;
  }
  try {
    setStatus("Mise à jour de la liquidité…");
    const resp = await fetch("/api/liquidite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ liquidite: value }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    state.liquidite = data.liquidite;
    setStatus("");
    renderSummary();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
});

const deleteDialog = document.getElementById("delete-dialog");
const deleteMessage = document.getElementById("delete-message");
let pendingDeleteId = null;

const editDialog = document.getElementById("edit-dialog");
const editTitle = document.getElementById("edit-title");
const editQuantity = document.getElementById("edit-quantity");
const editDate = document.getElementById("edit-date");
const editPrice = document.getElementById("edit-price");
const editDividend = document.getElementById("edit-dividend");
let pendingEditId = null;

tbody.addEventListener("click", (e) => {
  const delBtn = e.target.closest(".row-delete");
  if (delBtn) {
    pendingDeleteId = delBtn.dataset.id;
    deleteMessage.textContent =
      `« ${delBtn.dataset.name} » sera supprimée du portefeuille.`;
    if (typeof deleteDialog.showModal === "function") {
      deleteDialog.showModal();
    }
    return;
  }
  const editBtn = e.target.closest(".row-edit");
  if (editBtn) {
    pendingEditId = editBtn.dataset.id;
    editTitle.textContent = `Modifier « ${editBtn.dataset.name} »`;
    editQuantity.value = editBtn.dataset.quantity;
    editDate.value = toIsoDate(editBtn.dataset.date);
    editPrice.value = editBtn.dataset.price;
    editDividend.value = editBtn.dataset.dividend;
    if (typeof editDialog.showModal === "function") {
      editDialog.showModal();
      editQuantity.focus();
      editQuantity.select();
    }
  }
});

deleteDialog.addEventListener("close", async () => {
  const id = pendingDeleteId;
  pendingDeleteId = null;
  if (deleteDialog.returnValue !== "delete" || !id) return;
  try {
    setStatus("Suppression…");
    const resp = await fetch(
      `/api/wallet/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    setStatus("");
    await loadData();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
});

editDialog.addEventListener("close", async () => {
  const id = pendingEditId;
  pendingEditId = null;
  if (editDialog.returnValue !== "save" || !id) return;
  const body = {
    quantity: Number(editQuantity.value),
    date: editDate.value,
    price: Number(editPrice.value),
    dividend: Number(editDividend.value),
  };
  if (
    !Number.isFinite(body.quantity) ||
    !Number.isFinite(body.price) ||
    !Number.isFinite(body.dividend) ||
    !body.date
  ) {
    setStatus("Champs invalides", true);
    return;
  }
  try {
    setStatus("Mise à jour…");
    const resp = await fetch(`/api/wallet/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    setStatus("");
    await loadData();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
});

const addBtn = document.getElementById("add-action");
const addDialog = document.getElementById("add-dialog");
const addId = document.getElementById("add-id");
const addQuantity = document.getElementById("add-quantity");
const addDate = document.getElementById("add-date");
const addPrice = document.getElementById("add-price");
const addDividend = document.getElementById("add-dividend");

addBtn.addEventListener("click", async () => {
  try {
    setStatus("Chargement des actions disponibles…");
    const resp = await fetch("/api/stocks/available");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const stocks = await resp.json();
    const opts = ['<option value="">— Choisir une action —</option>'];
    for (const s of stocks) {
      opts.push(
        `<option value="${escapeHtml(s.id)}">` +
          `${escapeHtml(s.name)} (${escapeHtml(s.id)})</option>`,
      );
    }
    addId.innerHTML = opts.join("");
    addId.value = "";
    addQuantity.value = "";
    addDate.value = todayIso();
    addPrice.value = "";
    addDividend.value = "0";
    setStatus("");
    if (typeof addDialog.showModal === "function") {
      addDialog.showModal();
      addId.focus();
    }
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
});

addDialog.addEventListener("close", async () => {
  if (addDialog.returnValue !== "save") return;
  const body = {
    id: addId.value,
    quantity: Number(addQuantity.value),
    date: addDate.value,
    price: Number(addPrice.value),
    dividend: Number(addDividend.value),
  };
  if (
    !body.id ||
    !Number.isFinite(body.quantity) ||
    body.quantity <= 0 ||
    !Number.isFinite(body.price) ||
    !Number.isFinite(body.dividend) ||
    !body.date
  ) {
    setStatus("Champs invalides", true);
    return;
  }
  try {
    setStatus("Ajout…");
    const resp = await fetch("/api/wallet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    setStatus("");
    await loadData();
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
});

loadData();
