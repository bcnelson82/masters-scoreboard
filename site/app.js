const trashTalkLines = [
  "Might be time to switch sports.",
  "Somebody forgot how to putt.",
  "This is getting hard to watch.",
  "Masters called... they want better golf.",
  "Currently sponsored by bogeys.",
  "You guys practicing or competing?"
];

async function loadScores() {
  const res = await fetch('data/latest.json');
  const data = await res.json();

  updateTeam(data.team1, 'score1', 'players1');
  updateTeam(data.team2, 'score2', 'players2');

  applyGameLogic(data.team1.total, data.team2.total);
}

function updateTeam(team, scoreId, playersId) {
  document.getElementById(scoreId).innerText = team.total;

  const container = document.getElementById(playersId);
  container.innerHTML = '';

  team.players.forEach(p => {
    const div = document.createElement('div');
    div.className = 'player';

    div.innerHTML = `
      <div class="player-name">${p.name}</div>
      <div>
        <div class="player-score">${p.score}</div>
        <div class="status">${p.status}</div>
      </div>
    `;

    container.appendChild(div);
  });
}

function applyGameLogic(score1, score2) {
  const team1 = document.getElementById('team1');
  const team2 = document.getElementById('team2');

  const badge1 = document.getElementById('badge1');
  const badge2 = document.getElementById('badge2');

  const trash1 = document.getElementById('trash1');
  const trash2 = document.getElementById('trash2');

  team1.classList.remove('leading');
  team2.classList.remove('leading');
  badge1.style.display = 'none';
  badge2.style.display = 'none';
  trash1.innerText = '';
  trash2.innerText = '';

  if (score1 < score2) {
    team1.classList.add('leading');
    badge1.style.display = 'inline-block';
    badge1.innerText = 'LEADING';
    trash2.innerText = randomTrash();
  } else if (score2 < score1) {
    team2.classList.add('leading');
    badge2.style.display = 'inline-block';
    badge2.innerText = 'LEADING';
    trash1.innerText = randomTrash();
  } else {
    trash1.innerText = "Too close to call.";
    trash2.innerText = "Too close to call.";
  }
}

function randomTrash() {
  return trashTalkLines[Math.floor(Math.random() * trashTalkLines.length)];
}

loadScores();
setInterval(loadScores, 60000);
