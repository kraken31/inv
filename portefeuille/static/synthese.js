const root = document.getElementById("synthese");
const statusEl = document.getElementById("status");

const nfEur = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 2,
});
const nfPct = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const nfPerf = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});

const SLICES = [
  { key: "actions", label: "Actions", color: "#38bdf8" },
  { key: "etf", label: "ETF", color: "#a78bfa" },
  { key: "crypto", label: "Crypto", color: "#fbbf24" },
];

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

function portfolioUrl(key, owner) {
  const q = `proprietaire=${encodeURIComponent(owner)}`;
  if (key === "etf") return `/portefeuille-etf?${q}`;
  if (key === "crypto") return `/portefeuille-crypto?${q}`;
  return `/?${q}`;
}

function sliceLink(part, owner, inner) {
  const href = portfolioUrl(part.key, owner);
  const label = `Ouvrir le portefeuille ${part.label} de ${owner}`;
  return `<a href="${escapeHtml(href)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">${inner}</a>`;
}

function pieMarkup(parts, owner) {
  const total = parts.reduce((sum, part) => sum + part.amount, 0);
  if (total <= 0) {
    return '<p class="pie-empty">Aucune valorisation</p>';
  }
  const positive = parts.filter((part) => part.amount > 0);
  if (positive.length === 1) {
    const circle = `<circle cx="100" cy="100" r="92" fill="${positive[0].color}" />`;
    return `<svg class="pie" viewBox="0 0 200 200" role="img">${sliceLink(positive[0], owner, circle)}</svg>`;
  }

  const cx = 100;
  const cy = 100;
  const r = 92;
  let angle = -Math.PI / 2;
  const paths = [];
  for (const part of positive) {
    const sweep = (part.amount / total) * Math.PI * 2;
    const x1 = cx + r * Math.cos(angle);
    const y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(angle + sweep);
    const y2 = cy + r * Math.sin(angle + sweep);
    const large = sweep > Math.PI ? 1 : 0;
    const d = `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`;
    paths.push(sliceLink(part, owner, `<path d="${d}" fill="${part.color}" />`));
    angle += sweep;
  }
  return `<svg class="pie" viewBox="0 0 200 200" role="img">${paths.join("")}</svg>`;
}

function perfMarkup(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '<span class="pie-perf"></span>';
  }
  const n = Number(value);
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "";
  return `<span class="pie-perf ${cls}">${nfPerf.format(n)}\u00A0%</span>`;
}

function legendMarkup(parts, total, owner) {
  return parts.map((part) => {
    const pct = total > 0 ? (100 * part.amount) / total : 0;
    const muted = part.amount <= 0 ? " muted" : "";
    const inner = `
      <span class="swatch" style="background:${part.color}"></span>
      <span class="pie-label">${escapeHtml(part.label)}</span>
      <span class="pie-amount">${nfEur.format(part.amount)}</span>
      <span class="pie-pct">${nfPct.format(pct)}\u00A0%</span>
      ${perfMarkup(part.perf)}`;
    if (part.amount <= 0) {
      return `<li><span class="pie-legend-item${muted}">${inner}</span></li>`;
    }
    const href = portfolioUrl(part.key, owner);
    const label = `Ouvrir le portefeuille ${part.label} de ${owner}`;
    return `<li><a class="pie-legend-item" href="${escapeHtml(href)}" title="${escapeHtml(label)}">${inner}</a></li>`;
  }).join("");
}

function render(rows) {
  root.innerHTML = "";
  if (!rows.length) {
    root.innerHTML = '<p class="pie-empty">Aucun portefeuille</p>';
    return;
  }
  for (const row of rows) {
    const parts = SLICES.map((slice) => ({
      ...slice,
      amount: Number(row[slice.key]) || 0,
      perf: row[`${slice.key}_perf`],
    }));
    const total = Number(row.total) || 0;
    const card = document.createElement("article");
    card.className = "synthese-card";
    card.innerHTML = `
      <h2 class="page-subtitle">${escapeHtml(row.proprietaire)}</h2>
      ${pieMarkup(parts, row.proprietaire)}
      <ul class="pie-legend">${legendMarkup(parts, total, row.proprietaire)}</ul>
      <p class="pie-total">
        <span>Total ${nfEur.format(total)}</span>
        ${perfMarkup(row.total_perf)}
      </p>
    `;
    root.appendChild(card);
  }
}

async function loadData() {
  setStatus("Chargement…");
  try {
    const resp = await fetch("/api/synthese");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    render(await resp.json());
    setStatus("");
  } catch (e) {
    setStatus(`Erreur: ${e.message}`, true);
  }
}

loadData();
