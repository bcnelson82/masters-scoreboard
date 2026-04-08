const DATA_URL = "./data/latest.json";
const POLL_INTERVAL_MS = 60_000;

const overviewGrid = document.getElementById("overview-grid");
const teamGrid = document.getElementById("team-grid");
const heroSubtitle = document.getElementById("hero-subtitle");
const lastUpdated = document.getElementById("last-updated");
const refreshCadence = document.getElementById("refresh-cadence");
const sourceNote = document.getElementById("source-note");

const overviewTemplate = document.getElementById("overview-card-template");
const teamTemplate = document.getElementById("team-card-template");

function formatScore(value) {
  if (value === 0) return "E";
  if (value > 0) return `+${value}`;
  return String(value);
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(date);
}

function renderOverview(data) {
  overviewGrid.innerHTML = "";
  const leaderSlug = data.meta?.leaderSlug;
  const margin = data.meta?.margin;

  for (const [index, team] of data.teams.entries()) {
    const fragment = overviewTemplate.content.cloneNode(true);
    fragment.querySelector(".team-label").textContent = `Team ${index + 1}`;
    fragment.querySelector(".team-title").textContent = team.displayName;
    fragment.querySelector(".team-subtitle").textContent = team.subtitle || " ";
    fragment.querySelector(".score-value").textContent = team.totalScoreDisplay || formatScore(team.totalScoreToPar);

    const footer = fragment.querySelector(".overview-footer");
    const badge = fragment.querySelector(".leader-badge");

    if (leaderSlug && leaderSlug === team.slug) {
      badge.hidden = false;
      footer.textContent = typeof margin === "number" && margin > 0
        ? `Leading by ${margin} stroke${margin === 1 ? "" : "s"}.`
        : "Currently tied for the lead.";
    } else if (typeof margin === "number" && leaderSlug) {
      footer.textContent = `Chasing the lead.`;
    } else {
      footer.textContent = "Totals update automatically when the workflow runs.";
    }

    overviewGrid.appendChild(fragment);
  }
}

function renderTeams(data) {
  teamGrid.innerHTML = "";

  for (const [index, team] of data.teams.entries()) {
    const fragment = teamTemplate.content.cloneNode(true);
    fragment.querySelector(".team-label").textContent = `Team ${index + 1}`;
    fragment.querySelector(".team-title").textContent = team.displayName;
    fragment.querySelector(".team-subtitle").textContent = team.subtitle || " ";
    fragment.querySelector(".team-total").textContent = team.totalScoreDisplay || formatScore(team.totalScoreToPar);

    const tbody = fragment.querySelector("tbody");

    for (const player of team.players) {
      const row = document.createElement("tr");

      const nameCell = document.createElement("td");
      nameCell.innerHTML = `
        <span class="player-name">${player.name}</span>
        <span class="player-meta">${player.detail || player.status || ""}</span>
      `;

      const scoreCell = document.createElement("td");
      const scoreChip = document.createElement("span");
      scoreChip.className = "score-chip";
      scoreChip.textContent = player.scoreDisplay || formatScore(player.scoreToPar || 0);
      scoreCell.appendChild(scoreChip);

      const statusCell = document.createElement("td");
      const statusChip = document.createElement("span");
      statusChip.className = "status-chip";
      statusChip.textContent = player.status || "—";
      statusCell.appendChild(statusChip);

      row.append(nameCell, scoreCell, statusCell);
      tbody.appendChild(row);
    }

    teamGrid.appendChild(fragment);
  }
}

function renderMeta(data) {
  const mode = data.event?.mode === "tee-times"
    ? "Tee times loaded. Team totals will shift once live scoring appears."
    : (data.event?.scoringRule || "Live totals are based on each roster's combined score to par.");

  heroSubtitle.textContent = `${data.event?.name || "Tournament"}: ${mode}`;
  lastUpdated.textContent = formatTimestamp(data.meta?.fetchedAtUtc);
  refreshCadence.textContent = `${data.event?.refreshMinutes || 10} min`;

  const sourceBits = [];
  if (data.event?.sourceUrl) {
    sourceBits.push(`Source: ${data.event.sourceUrl}`);
  }
  if (data.event?.scoringRule) {
    sourceBits.push(data.event.scoringRule);
  }
  sourceNote.textContent = sourceBits.join(" • ");
}

async function loadScoreboard() {
  const response = await fetch(`${DATA_URL}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`);
  }
  return response.json();
}

function renderError(message) {
  heroSubtitle.textContent = "The page could not load the latest scoreboard JSON.";
  lastUpdated.textContent = "Unavailable";
  refreshCadence.textContent = "Check workflow";
  sourceNote.textContent = message;
  overviewGrid.innerHTML = `<p class="empty-state">${message}</p>`;
  teamGrid.innerHTML = "";
}

async function refresh() {
  try {
    const data = await loadScoreboard();
    renderMeta(data);
    renderOverview(data);
    renderTeams(data);
  } catch (error) {
    renderError(error instanceof Error ? error.message : "Unknown error.");
  }
}

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
