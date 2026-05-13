// Utilitaire d'export CSV partagé par les pages PER, RSI, Rendement
// et Sécurité. Lit les en-têtes du <thead> (texte affiché + data-key)
// et sérialise les lignes fournies (généralement state.filtered, donc
// ce qui est visible à l'écran après filtre/tri).
//
// Sortie : CSV standard (séparateur virgule, point comme décimale,
// BOM UTF-8 pour qu'Excel ouvre les accents correctement).

function csvEscape(v) {
  if (v == null) return "";
  const s = String(v);
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function csvCell(v) {
  if (v == null) return "";
  if (typeof v === "number") {
    return Number.isFinite(v) ? String(v) : "";
  }
  return csvEscape(v);
}

function todayStamp() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function exportTableToCsv(tableSelector, rows, baseName) {
  const ths = document.querySelectorAll(`${tableSelector} thead th`);
  const headers = [];
  const keys = [];
  ths.forEach((th) => {
    headers.push(th.textContent.trim());
    keys.push(th.dataset.key || "");
  });

  const lines = [headers.map(csvEscape).join(",")];
  for (const r of rows) {
    lines.push(keys.map((k) => csvCell(k ? r[k] : "")).join(","));
  }

  const blob = new Blob(["\ufeff" + lines.join("\n")], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${baseName}-${todayStamp()}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
