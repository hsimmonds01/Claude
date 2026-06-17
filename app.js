const STAGE_KNOCKOUT_BONUS = {
  ROUND_OF_32: 4,
  LAST_32: 4,
  ROUND_OF_16: 6,
  LAST_16: 6,
  QUARTER_FINALS: 10,
  SEMI_FINALS: 15,
  FINAL: 23,
};

const normalize = (s) =>
  (s || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9 ]/g, "")
    .trim();

function matchesTeam(apiName, aliases) {
  const n = normalize(apiName);
  return aliases.some((alias) => normalize(alias) === n);
}

function scoreTeam(team, matches) {
  let groupPoints = 0;
  let knockoutBonus = 0;
  let thirdPlaceBonus = 0;
  let displayName = team.label;

  for (const m of matches) {
    if (m.status !== "FINISHED") continue;
    const isHome = matchesTeam(m.homeTeam, team.aliases);
    const isAway = matchesTeam(m.awayTeam, team.aliases);
    if (!isHome && !isAway) continue;

    displayName = isHome ? m.homeTeam : m.awayTeam;

    const won =
      (isHome && m.winner === "HOME_TEAM") || (isAway && m.winner === "AWAY_TEAM");
    const drew = m.winner === "DRAW";

    if (m.stage === "GROUP_STAGE") {
      if (won) groupPoints += 3;
      else if (drew) groupPoints += 1;
    } else if (m.stage === "THIRD_PLACE") {
      if (won) thirdPlaceBonus = 2;
    } else if (won) {
      const bonus = STAGE_KNOCKOUT_BONUS[m.stage] || 0;
      knockoutBonus = Math.max(knockoutBonus, bonus);
    }
  }

  return {
    displayName,
    groupPoints,
    knockoutBonus,
    thirdPlaceBonus,
    total: groupPoints + knockoutBonus + thirdPlaceBonus,
  };
}

function renderLeaderboard(players, matches) {
  const board = document.getElementById("leaderboard");
  board.innerHTML = "";

  const scored = players
    .map((player) => {
      const teams = player.teams.map((t) => scoreTeam(t, matches));
      const total = teams.reduce((sum, t) => sum + t.total, 0);
      return { name: player.name, teams, total };
    })
    .sort((a, b) => b.total - a.total);

  scored.forEach((player, i) => {
    const card = document.createElement("div");
    card.className = "player-card";

    const row = document.createElement("div");
    row.className = "player-row";
    row.innerHTML = `
      <span class="rank ${i === 0 ? "gold" : ""}">${i + 1}</span>
      <span class="player-name">${i === 0 ? '<span class="leader-trophy" aria-label="Leader">🏆</span>' : ""}${player.name}</span>
      <span class="player-total">${player.total} pts</span>
      <span class="chevron">▶</span>
    `;
    row.addEventListener("click", () => card.classList.toggle("open"));

    const breakdown = document.createElement("div");
    breakdown.className = "team-breakdown";
    breakdown.innerHTML = player.teams
      .map(
        (t) => `
        <div class="team-row">
          <span>${t.displayName}</span>
          <span class="team-points">${t.groupPoints} group + ${t.knockoutBonus} round + ${t.thirdPlaceBonus} 3rd = <strong>${t.total}</strong></span>
        </div>`
      )
      .join("");

    card.append(row, breakdown);
    board.appendChild(card);
  });

  if (scored.length === 0) {
    board.innerHTML = '<p class="empty-state">No player data found.</p>';
  }
}

const STAGE_LABELS = {
  GROUP_STAGE: "Group",
  ROUND_OF_32: "R32",
  LAST_32: "R32",
  ROUND_OF_16: "R16",
  LAST_16: "R16",
  QUARTER_FINALS: "QF",
  SEMI_FINALS: "SF",
  THIRD_PLACE: "3rd",
  FINAL: "Final",
};

function renderResults(matches) {
  const list = document.getElementById("results");
  list.innerHTML = "";

  const sorted = [...matches].sort((a, b) => new Date(b.utcDate) - new Date(a.utcDate));

  if (sorted.length === 0) {
    list.innerHTML = '<p class="empty-state">No match data yet.</p>';
    return;
  }

  list.innerHTML = sorted
    .map((m) => {
      const stageLabel = STAGE_LABELS[m.stage] || m.stage;
      const isLive = m.status === "IN_PLAY" || m.status === "PAUSED";
      const isFinished = m.status === "FINISHED" || isLive;
      const scoreOrTime = isFinished
        ? `${m.homeScore ?? "-"} : ${m.awayScore ?? "-"}`
        : new Date(m.utcDate).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
      const statusText = isLive ? "LIVE" : m.status === "FINISHED" ? "Full time" : "Upcoming";

      return `
        <div class="result-row">
          <span class="result-stage">${stageLabel}</span>
          <span class="result-teams">${m.homeTeam} vs ${m.awayTeam}</span>
          <span class="result-score">${scoreOrTime}</span>
          <span class="result-status ${isLive ? "live" : ""}">${statusText}</span>
        </div>`;
    })
    .join("");
}

function setupTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.id !== btn.dataset.tab;
      });
    });
  });
}

function formatTimestamp(iso) {
  if (!iso) return "Not yet synced";
  const d = new Date(iso);
  return `Last updated ${d.toLocaleString()}`;
}

async function loadData() {
  const statusEl = document.getElementById("last-updated");
  const refreshBtn = document.getElementById("refresh-btn");
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Refreshing…";
  try {
    const [playersRes, standingsRes] = await Promise.all([
      fetch("config/players.json", { cache: "no-store" }),
      fetch("data/standings.json", { cache: "no-store" }),
    ]);

    if (!playersRes.ok || !standingsRes.ok) {
      throw new Error("Failed to load data files");
    }

    const { players } = await playersRes.json();
    const { lastUpdated, matches } = await standingsRes.json();

    renderLeaderboard(players, matches || []);
    renderResults(matches || []);
    statusEl.textContent = formatTimestamp(lastUpdated);
  } catch (err) {
    statusEl.textContent = "Unable to load standings — showing last known state.";
    console.error(err);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh";
  }
}

document.getElementById("refresh-btn").addEventListener("click", loadData);
setupTabs();
loadData();
