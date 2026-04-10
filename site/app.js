const trashTalkLines = [
  "Currently searching for fairways, confidence, and a small miracle.",
  "This side appears to be honoring tradition by donating strokes.",
  "The green jacket committee has declined to comment.",
  "Strong clubhouse vibes. Less strong golf vibes.",
  "This card is proudly powered by missed putts.",
  "At this point, bogey avoidance would count as momentum.",
  "Someone check whether the range session ever actually ended.",
  "A bold strategy to let the other side feel comfortable."
];

function scoreToNumber(score) {
  if (score === null || score === undefined) return 9999;
  const s = String(score).trim().toUpperCase();
  if (s === "E") return 0;
  if (s === "--" || s === "CUT" || s === "WD" || s === "DQ" || s === "MDF" || s === "DNS") return 9999;
  const n = Number(s);
  return Number.isNaN(n) ? 9999 : n;
}

function formatScore(score) {
  if (score === null || score === undefined) return "--";
  const s = String(score).trim().toUpperCase();
  if (s === "E" || s === "CUT" || s === "WD" || s === "DQ" || s === "MDF" || s === "DNS" || s === "--") return s;
  const n = Number(s);
  if (Number.isNaN(n)) return s;
  if (n > 0) return `+${n}`;
  return `${n}`;
}

function randomTrash() {
  return trashTalkLines[Math.floor(Math.random() * trashTalkLines.length)];
}

function safeTimestamp(ts) {
  if (!ts) return "Updated just now";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return `Updated ${ts}`;
  return `Updated ${date.toLocaleString()}`;
}

function getTopPerformer(players) {
  if (!players || !players.length) return null;
  const sorted = [...players].sort((a, b) => scoreToNumber(a.score) - scoreToNumber(b.score));
  return sorted[0] || null;
}

function sortPlayers(players) {
  return [...players].sort((a, b) => {
    const scoreDiff = scoreToNumber(a.score) - scoreToNumber(b.score);
    if (scoreDiff !== 0) return scoreDiff;
    return String(a.name).localeCompare(String(b.name));
  });
}

function pulseScore(el) {
  el.classList.remove("pulse");
  void el.offsetWidth;
  el.classList.add("pulse");
  setTimeout(() => el.classList.remove("pulse"), 240);
}

function normalizePlayer(p) {
  return {
    name: p.name,
    score: p.scoreDisplay,
    numericScore: p.scoreToPar,
    status: p.detail || p.status || "Waiting to tee off"
  };
}

function normalizeTeam(team) {
  const players = (team.players || []).map(normalizePlayer);
  return {
    name: team.displayName,
    total: team.totalScoreDisplay,
    numericTotal: team.totalScoreToPar,
    players
  };
}

function renderTeam(team, ids) {
  const scoreEl = document.getElementById(ids.score);
  const playersEl = document.getElementById(ids.players);
  const topEl = document.getElementById(ids.top);
  const countEl = document.getElementById(ids.count);

  scoreEl.textContent = formatScore(team.total);
  pulseScore(scoreEl);

  const players = sortPlayers(team.players || []);
  const topPlayer = getTopPerformer(players);

  topEl.textContent = topPlayer
    ? `${topPlayer.name} (${formatScore(topPlayer.score)})`
    : "—";

  countEl.textContent = `${players.length} players`;

  playersEl.innerHTML = "";

  players.forEach((p, index) => {
    const row = document.createElement("div");
    row.className = "player-row";

    const topBadge = index === 0
      ? `<span class="top-badge">Top Form</span>`
      : "";

    row.innerHTML = `
  <div class="player-left">
    <div class="player-name-line">
      <div class="player-name">${p.name}</div>
      ${topBadge}
    </div>
  </div>
  <div class="player-right">
    <div class="player-score">${formatScore(p.score)}</div>
    <div class="player-rank-note">${p.status || "Waiting to tee off"}</div>
  </div>
`;

    playersEl.appendChild(row);
  });
}

function applyGameLogic(team1, team2) {
  const card1 = document.getElementById("team1");
  const card2 = document.getElementById("team2");
  const badge1 = document.getElementById("badge1");
  const badge2 = document.getElementById("badge2");
  const trash1 = document.getElementById("trash1");
  const trash2 = document.getElementById("trash2");

  const s1 = team1.numericTotal;
  const s2 = team2.numericTotal;

  card1.classList.remove("leading", "trailing");
  card2.classList.remove("leading", "trailing");
  badge1.style.display = "none";
  badge2.style.display = "none";
  trash1.textContent = "";
  trash2.textContent = "";

  if (s1 < s2) {
    card1.classList.add("leading");
    card2.classList.add("trailing");
    badge1.style.display = "inline-flex";
    badge1.textContent = "Leading";
    trash2.textContent = randomTrash();
  } else if (s2 < s1) {
    card2.classList.add("leading");
    card1.classList.add("trailing");
    badge2.style.display = "inline-flex";
    badge2.textContent = "Leading";
    trash1.textContent = randomTrash();
  } else {
    trash1.textContent = "All square. Nobody gets chesty just yet.";
    trash2.textContent = "Dead heat. Tension levels: appropriately high.";
  }
}

function detectEventStatus(team1, team2) {
  const allPlayers = [
    ...(team1.players || []),
    ...(team2.players || [])
  ];

  const statuses = allPlayers.map(p => String(p.status || "").trim()).filter(Boolean);

  if (!statuses.length) return "Live Scoring";

  const live = statuses.find(s => /thru|live|round|tee time/i.test(s));
  if (live) return live;

  const complete = statuses.find(s => /complete|final|finished|cut|wd|dq/i.test(s));
  if (complete) return complete;

  return "Live Scoring";
}

async function loadScores() {
  try {
    const res = await fetch("data/latest.json", { cache: "no-store" });
    const data = await res.json();

    const team1 = normalizeTeam(data.teams[0]);
    const team2 = normalizeTeam(data.teams[1]);

    renderTeam(team1, {
      score: "score1",
      players: "players1",
      top: "top1",
      count: "players-count-1"
    });

    renderTeam(team2, {
      score: "score2",
      players: "players2",
      top: "top2",
      count: "players-count-2"
    });

    applyGameLogic(team1, team2);

    document.getElementById("updated-at").textContent =
      safeTimestamp(data.meta?.fetchedAtUtc);

    document.getElementById("event-status").textContent =
      detectEventStatus(team1, team2);
  } catch (err) {
    document.getElementById("updated-at").textContent =
      "Could not load latest scoring data";
    console.error(err);
  }
}

loadScores();
setInterval(loadScores, 60000);
