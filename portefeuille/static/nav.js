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

function trackRefresh(buttonId, statusId, job) {
  return setupRefreshButton(buttonId, statusId, job);
}

const byJob = {};
for (const ctrl of [
  trackRefresh("refresh-pricing", "refresh-pricing-status", "pricing"),
  trackRefresh("refresh-pricing-us", "refresh-pricing-us-status", "pricing_us"),
  trackRefresh("refresh-pricing-etf", "refresh-pricing-etf-status", "pricing_etf"),
  trackRefresh("refresh-pricing-crypto", "refresh-pricing-crypto-status", "pricing_crypto"),
  trackRefresh("refresh-dividends", "refresh-dividends-status", "dividends"),
  trackRefresh("refresh-results", "refresh-results-status", "results"),
  trackRefresh("refresh-dividends-us", "refresh-dividends-us-status", "dividends_us"),
  trackRefresh("refresh-results-us", "refresh-results-us-status", "results_us"),
]) {
  if (ctrl) byJob[ctrl.job] = ctrl;
}

// Les cours Paris et US partent après les résultats du même marché,
// pour que le PER soit calculé avec les comptes juste téléchargés.
const COURS_APRES_RESULTATS = {
  results: "pricing",
  results_us: "pricing_us",
};

const PRICING_LABELS = {
  pricing: "Cours Paris",
  pricing_us: "Cours US",
  pricing_etf: "ETF",
  pricing_crypto: "Crypto",
  dividends: "Div. Paris",
  results: "Rés. Paris",
  dividends_us: "Div. US",
  results_us: "Rés. US",
};

function setupRefreshAllButton(jobs, coursApresResultats) {
  const button = document.getElementById("refresh-pricing-all");
  const status = document.getElementById("refresh-pricing-all-status");
  const controls = jobs.map((job) => byJob[job]).filter(Boolean);
  if (!button || !status || controls.length === 0) return;

  const deferredJobs = new Set(Object.values(coursApresResultats));
  const immediate = controls.filter((ctrl) => !deferredJobs.has(ctrl.job));

  let pollTimer = null;
  let polling = false;
  let armed = false;
  const seenRunning = new Set();
  const startFailed = new Set();
  const coursLaunched = new Set();

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

  function coursStillPending() {
    return Object.values(coursApresResultats).some((job) => !coursLaunched.has(job));
  }

  function applySummary(states) {
    const waitingCours = armed && coursStillPending();
    const running = states.some((s) => s.running) || waitingCours;
    if (running) {
      button.disabled = true;
      button.classList.add("running");
      const parts = states.filter((s) => s.running).map(progressLabel);
      if (waitingCours && parts.length === 0) parts.push("En cours…");
      setStatus(parts.join(" · ") || "En cours…", "log");
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
    } else if (armed || (states.length && states.every((s) => s.exit_code === 0))) {
      setStatus("Terminé ✓", "success");
    } else {
      setStatus("");
    }
    armed = false;
  }

  async function startJob(ctrl) {
    const resp = await fetch(ctrl.url, { method: "POST" });
    const data = await resp.json();
    if (!resp.ok && resp.status !== 409) {
      throw new Error(data.error || ctrl.job);
    }
    ctrl.applyState(data);
    return data;
  }

  async function launchPendingCours(states) {
    const starts = [];
    for (const [resultJob, coursJob] of Object.entries(coursApresResultats)) {
      if (coursLaunched.has(coursJob)) continue;
      const ctrl = byJob[coursJob];
      if (!ctrl) continue;
      const state = states.find((s) => s.job === resultJob);
      if (state && state.running) seenRunning.add(resultJob);
      const resultatsTermines = seenRunning.has(resultJob) && state && !state.running;
      if (!resultatsTermines && !startFailed.has(resultJob)) continue;
      coursLaunched.add(coursJob);
      starts.push(
        startJob(ctrl).catch((e) => {
          coursLaunched.delete(coursJob);
          throw e;
        }),
      );
    }
    if (!starts.length) return false;
    await Promise.all(starts);
    return true;
  }

  async function fetchStatuses() {
    if (polling) return;
    polling = true;
    try {
      const states = [];
      for (const ctrl of controls) {
        const resp = await fetch(ctrl.url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        states.push(data);
        ctrl.applyState(data);
      }
      if (armed && (await launchPendingCours(states))) {
        polling = false;
        return fetchStatuses();
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
      armed = false;
    } finally {
      polling = false;
    }
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    armed = true;
    seenRunning.clear();
    startFailed.clear();
    coursLaunched.clear();
    setStatus("Démarrage…");
    const errors = [];
    for (const ctrl of immediate) {
      try {
        const data = await startJob(ctrl);
        if (data.running && COURS_APRES_RESULTATS[ctrl.job]) {
          seenRunning.add(ctrl.job);
        }
      } catch (e) {
        errors.push(e.message);
        if (COURS_APRES_RESULTATS[ctrl.job]) startFailed.add(ctrl.job);
      }
    }
    if (errors.length === immediate.length) {
      button.disabled = false;
      armed = false;
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

setupRefreshAllButton(
  [
    "pricing",
    "pricing_us",
    "pricing_etf",
    "pricing_crypto",
    "dividends",
    "results",
    "dividends_us",
    "results_us",
  ],
  COURS_APRES_RESULTATS,
);
