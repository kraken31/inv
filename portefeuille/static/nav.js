/**
 * Boutons de refresh de la barre de menu.
 *
 * Chaque bouton est associé à un "job" côté serveur (route
 * /api/refresh/<job>) et à un span de statut. Le helper
 * `setupRefreshButton` câble :
 *   - clic du bouton  -> POST /api/refresh/<job>
 *   - chargement page -> GET  /api/refresh/<job> (refléter un éventuel
 *                         refresh déjà en cours, lancé depuis un autre
 *                         onglet ou avant un rechargement)
 *   - polling 1.5 s pendant l'exécution (effet "tail -f" via le champ
 *     last_log renvoyé par le serveur)
 *
 * Plusieurs jobs peuvent tourner en parallèle (ils ont chacun leur
 * propre subprocess et leur propre lock côté backend).
 */
function setupRefreshButton(buttonId, statusId, job) {
  const button = document.getElementById(buttonId);
  const status = document.getElementById(statusId);
  if (!button || !status) return;

  const url = `/api/refresh/${encodeURIComponent(job)}`;
  let pollTimer = null;

  function setStatus(text, cls = "") {
    status.textContent = text;
    status.className = "nav-status" + (cls ? ` ${cls}` : "");
    status.title = text;
  }

  function startPolling() {
    if (!pollTimer) pollTimer = setInterval(fetchStatus, 1500);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function applyState(data) {
    if (data.running) {
      button.disabled = true;
      button.classList.add("running");
      // Tant que le log n'a pas encore de ligne avec compteur (subprocess
      // vient juste de démarrer, ou ligne d'en-tête type "632 actions à
      // traiter"), on affiche un message d'attente. Sinon on n'affiche
      // que le compteur d'avancement en tête de ligne ("[i/n]"), beaucoup
      // plus lisible que la ligne complète dans la barre de nav.
      const counter = data.last_log && data.last_log.match(/^\[\d+\/\d+\]/);
      if (counter) {
        setStatus(counter[0], "log");
      } else {
        setStatus("En cours…");
      }
      startPolling();
    } else {
      button.disabled = false;
      button.classList.remove("running");
      stopPolling();
      if (data.exit_code === 0) {
        setStatus("Terminé ✓", "success");
      } else if (data.exit_code != null) {
        setStatus(`Échec (code ${data.exit_code})`, "error");
      } else {
        setStatus("");
      }
    }
  }

  async function fetchStatus() {
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      applyState(await resp.json());
    } catch (e) {
      stopPolling();
      button.disabled = false;
      button.classList.remove("running");
      setStatus(`Erreur: ${e.message}`, "error");
    }
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    setStatus("Démarrage…");
    try {
      const resp = await fetch(url, { method: "POST" });
      const data = await resp.json();
      // 202 = démarré, 409 = déjà en cours (état déjà valide).
      if (!resp.ok && resp.status !== 409) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      applyState(data);
    } catch (e) {
      button.disabled = false;
      setStatus(`Erreur: ${e.message}`, "error");
    }
  });

  fetchStatus();
  return { job, url, applyState, fetchStatus };
}

const pricingControls = [
  setupRefreshButton("refresh-pricing", "refresh-pricing-status", "pricing"),
  setupRefreshButton("refresh-pricing-etf", "refresh-pricing-etf-status", "pricing_etf"),
  setupRefreshButton("refresh-pricing-crypto", "refresh-pricing-crypto-status", "pricing_crypto"),
].filter(Boolean);

setupRefreshButton("refresh-dividends", "refresh-dividends-status", "dividends");
setupRefreshButton("refresh-results", "refresh-results-status", "results");

const PRICING_LABELS = {
  pricing: "Actions",
  pricing_etf: "ETF",
  pricing_crypto: "Crypto",
};

function setupRefreshAllButton(controls) {
  const button = document.getElementById("refresh-pricing-all");
  const status = document.getElementById("refresh-pricing-all-status");
  if (!button || !status || controls.length === 0) return;

  let pollTimer = null;

  function setStatus(text, cls = "") {
    status.textContent = text;
    status.className = "nav-status" + (cls ? ` ${cls}` : "");
    status.title = text;
  }

  function progressLabel(data) {
    const name = PRICING_LABELS[data.job] || data.job;
    if (data.running) {
      const counter = data.last_log && data.last_log.match(/^\[(\d+\/\d+)\]/);
      return counter ? `${name} ${counter[1]}` : `${name}…`;
    }
    if (data.exit_code === 0) return `${name} ✓`;
    if (data.exit_code != null) return `${name} échec`;
    return `${name}…`;
  }

  function applySummary(states) {
    const running = states.some((s) => s.running);
    if (running) {
      button.disabled = true;
      button.classList.add("running");
      setStatus(states.map(progressLabel).join(" · "), "log");
      if (!pollTimer) pollTimer = setInterval(fetchStatuses, 1500);
      return;
    }
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    button.disabled = false;
    button.classList.remove("running");
    const failed = states.filter((s) => s.exit_code != null && s.exit_code !== 0);
    if (failed.length) {
      setStatus(failed.map(progressLabel).join(" · "), "error");
    } else if (states.length && states.every((s) => s.exit_code === 0)) {
      setStatus("Terminé ✓", "success");
    } else {
      setStatus("");
    }
  }

  async function fetchStatuses() {
    try {
      const states = [];
      for (const ctrl of controls) {
        const resp = await fetch(ctrl.url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        states.push(data);
        ctrl.applyState(data);
      }
      applySummary(states);
    } catch (e) {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      button.disabled = false;
      button.classList.remove("running");
      setStatus(`Erreur: ${e.message}`, "error");
    }
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    setStatus("Démarrage…");
    const errors = [];
    for (const ctrl of controls) {
      try {
        const resp = await fetch(ctrl.url, { method: "POST" });
        const data = await resp.json();
        if (!resp.ok && resp.status !== 409) {
          errors.push(data.error || ctrl.job);
          continue;
        }
        ctrl.applyState(data);
      } catch (e) {
        errors.push(e.message);
      }
    }
    if (errors.length === controls.length) {
      button.disabled = false;
      setStatus(`Erreur: ${errors[0]}`, "error");
      return;
    }
    if (errors.length) {
      setStatus(`Partiel: ${errors.join(", ")}`, "error");
    }
    fetchStatuses();
  });

  fetchStatuses();
}

setupRefreshAllButton(pricingControls);
