async function loadScores() {
  const res = await fetch('data/latest.json');
  const data = await res.json();

  updateTeam(data.team1, 'score1', 'players1');
  updateTeam(data.team2, 'score2', 'players2');
}

function updateTeam(team, scoreId, playersId) {
  document.getElementById(scoreId).innerText = team.total;

  const container = document.getElementById(playersId);
  container.innerHTML = '';

  team.players.forEach(p => {
    const div = document.createElement('div');
    div.className = 'player';

    div.innerHTML = `
      <span>${p.name} (${p.score})</span>
      <span class="status">${p.status}</span>
    `;

    container.appendChild(div);
  });
}

loadScores();
setInterval(loadScores, 60000);
