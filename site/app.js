const TEAM_LABELS = {
  team1: "Dave's Duffers",
  team2: "Brian's Ballers"
};

let previousScores = {
  score1: null,
  score2: null
};

function formatScore(score) {
  if (score === null || score === undefined || score === "") return "—";
  const str = String(score).trim();

  if (str === "E") return "E";
  if (str.startsWith("-") || str.startsWith("+")) return str;

  const num = Number(str);
  if (!Number.isNaN(num)) {
    if (num > 0) return `+${num}`;
    if (num === 0) return "E";
    return `${num}`;
  }

  return str;
}

function scoreClass(score) {
  const str = formatScore(score);
  if (str === "E") return "even";
  if (str.startsWith("-")) return "under";
  if (str.startsWith("+")) return "over";
  return "";
}

function parseNumericScore(score) {
  const str = formatScore(score);
  if (str === "E") return 0;
  const num = Number(str);
  return Number.isNaN(num) ? null : num;
}

function updateTimestamp() {
  const el = document.getElementById("lastUpdated");
  const now = new Date();
  el.textContent = now.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function animateScore(id, nextValue) {
  const el = document.getElementById(id);
  if (!el) return;

  if (previousScores[id] !== null && previousScores[id] !== nextValue) {
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 320);
  }

  previousScores[id] = nextValue;
  el.textContent = formatScore(nextValue);
}

function buildPlayerRow(player) {
  const row = document.createElement("div");
  row.className = "player-row";

  const score = formatScore(player.score);
  const scorePillClass = scoreClass(player.score);

  row.innerHTML = `
    <div class="player-main">
      <div class="player-name-line">
        <div class="player-name">${player.name || "Unknown Player"}</div>
        <div class="score-pill ${scorePillClass}">${score}</div>
      </div>
      <div class="player-status">
        <strong>Status:</strong> ${player.status || "No live status"}
      </div>
    </div>
    <div class="player-side">
      <div class="status-chip">${player.status || "Waiting"}</div>
    </div>
  `;

  return row;
}

function sortPlayers(players = []) {
  return [...players].sort((a, b) => {
    const aNum = parseNumericScore(a.score);
    const bNum = parseNumericScore(b.score);

    if (aNum === null && bNum === null) return (a.name || "").localeCompare(b.name || "");
    if (aNum === null) return 1;
    if (bNum === null) return -1;
    if (aNum !== bNum) return aNum - bNum;

    return (a.name || "").localeCompare(b.name || "");
  });
}

function renderTeam(team, scoreId, playersId, fallbackName) {
  const scoreValue = team?.total ?? team?.score ?? "—";
  animateScore(scoreId, scoreValue);

  const list = document.getElementById(playersId);
  list.innerHTML = "";

  const players = sortPlayers(team?.players || []);
  if (!players.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No player data available yet.";
    list.appendChild(empty);
