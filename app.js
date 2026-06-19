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
  let gamesPlayed = 0;
  let displayName = team.label;

  for (const m of matches) {
    if (m.status !== "FINISHED") continue;
    const isHome = matchesTeam(m.homeTeam, team.aliases);
    const isAway = matchesTeam(m.awayTeam, team.aliases);
    if (!isHome && !isAway) continue;

    displayName = isHome ? m.homeTeam : m.awayTeam;
    gamesPlayed += 1;

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
    gamesPlayed,
    total: groupPoints + knockoutBonus + thirdPlaceBonus,
  };
}

const ISO_CODE_BY_LABEL = {
  Morocco: "MA",
  USA: "US",
  Ecuador: "EC",
  Tunisia: "TN",
  Spain: "ES",
  Mexico: "MX",
  "Ivory Coast": "CI",
  Czechia: "CZ",
  Netherlands: "NL",
  Switzerland: "CH",
  Austria: "AT",
  "New Zealand": "NZ",
  Portugal: "PT",
  Japan: "JP",
  Senegal: "SN",
  "DR Congo": "CD",
  Argentina: "AR",
  Turkey: "TR",
  Egypt: "EG",
  Iran: "IR",
  Canada: "CA",
  Bosnia: "BA",
  Brazil: "BR",
  Norway: "NO",
  Australia: "AU",
  Haiti: "HT",
  Belgium: "BE",
  Uruguay: "UY",
  "South Korea": "KR",
  Ghana: "GH",
  Germany: "DE",
  Croatia: "HR",
  Algeria: "DZ",
  Paraguay: "PY",
  France: "FR",
  Colombia: "CO",
  Sweden: "SE",
  "South Africa": "ZA",
};

const SPECIAL_FLAGS_BY_LABEL = {
  England: "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
  Scotland: "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
};

function flagFromIsoCode(code) {
  return code
    .toUpperCase()
    .split("")
    .map((c) => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65))
    .join("");
}

function flagForLabel(label) {
  if (SPECIAL_FLAGS_BY_LABEL[label]) return SPECIAL_FLAGS_BY_LABEL[label];
  const code = ISO_CODE_BY_LABEL[label];
  return code ? flagFromIsoCode(code) : "";
}

let flagByNormalizedAlias = new Map();

function buildFlagLookup(players) {
  flagByNormalizedAlias = new Map();
  for (const player of players) {
    for (const team of player.teams) {
      const flag = flagForLabel(team.label);
      if (!flag) continue;
      for (const alias of team.aliases) {
        flagByNormalizedAlias.set(normalize(alias), flag);
      }
    }
  }
}

function flagFor(name) {
  return flagByNormalizedAlias.get(normalize(name)) || "";
}

const PREV_RANKS_KEY = "wc-fantasy-prev-ranks";

function renderRankChange(name, rank, prevRanks) {
  const prevRank = prevRanks[name];
  if (prevRank === undefined || prevRank === rank) return "";
  return prevRank > rank
    ? '<span class="rank-change up" title="Moved up since last refresh">▲</span>'
    : '<span class="rank-change down" title="Moved down since last refresh">▼</span>';
}

function renderLeaderboard(players, matches) {
  const board = document.getElementById("leaderboard");
  board.innerHTML = "";

  const scored = players
    .map((player) => {
      const teams = player.teams.map((t) => scoreTeam(t, matches));
      const total = teams.reduce((sum, t) => sum + t.total, 0);
      const gamesPlayed = teams.reduce((sum, t) => sum + t.gamesPlayed, 0);
      return { name: player.name, teams, total, gamesPlayed };
    })
    .sort((a, b) => b.total - a.total);

  const prevRanks = JSON.parse(localStorage.getItem(PREV_RANKS_KEY) || "{}");

  scored.forEach((player, i) => {
    const rank = i + 1;
    const card = document.createElement("div");
    card.className = "player-card";

    const row = document.createElement("div");
    row.className = "player-row";
    row.innerHTML = `
      <span class="rank ${i === 0 ? "gold" : ""}">${rank}</span>
      <span class="player-name">${i === 0 ? '<span class="leader-trophy" aria-label="Leader">🏆</span>' : ""}${player.name}${renderRankChange(player.name, rank, prevRanks)}</span>
      <span class="games-badge" title="Games played by this player's teams">⚽ ${player.gamesPlayed}</span>
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
          <span>${flagFor(t.displayName)} ${t.displayName} <span class="team-games">(${t.gamesPlayed} played)</span></span>
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

  const currentRanks = {};
  scored.forEach((player, i) => {
    currentRanks[player.name] = i + 1;
  });
  localStorage.setItem(PREV_RANKS_KEY, JSON.stringify(currentRanks));
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

const LIVE_WINDOW_MS = 3 * 60 * 60 * 1000; // covers 90min + ET/pens + stoppage + fetch delay margin

function isMatchPlausiblyLive(m) {
  if (m.status !== "IN_PLAY" && m.status !== "PAUSED") return false;
  const kickoff = new Date(m.utcDate).getTime();
  if (Number.isNaN(kickoff)) return false;
  return Date.now() - kickoff <= LIVE_WINDOW_MS;
}

function hasStarted(m) {
  return m.status === "FINISHED" || m.status === "IN_PLAY" || m.status === "PAUSED";
}

function renderResultRow(m) {
  const stageLabel = STAGE_LABELS[m.stage] || m.stage;
  const isLive = isMatchPlausiblyLive(m);
  const started = hasStarted(m);
  const scoreOrTime = started
    ? `${m.homeScore ?? "-"} : ${m.awayScore ?? "-"}`
    : new Date(m.utcDate).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
  const statusText = isLive ? "LIVE" : started ? "Full time" : "Upcoming";

  return `
    <div class="result-row">
      <span class="result-stage">${stageLabel}</span>
      <span class="result-teams">${flagFor(m.homeTeam)} ${m.homeTeam} vs ${flagFor(m.awayTeam)} ${m.awayTeam}</span>
      <span class="result-score">${scoreOrTime}</span>
      <span class="result-status ${isLive ? "live" : ""}">${statusText}</span>
    </div>`;
}

let latestMatches = [];

function filterMatchesByTeam(matches, query) {
  const nq = normalize(query);
  if (!nq) return matches;
  return matches.filter(
    (m) => normalize(m.homeTeam).includes(nq) || normalize(m.awayTeam).includes(nq)
  );
}

function renderResults(matches) {
  latestMatches = matches;
  const query = document.getElementById("results-search").value;
  const filtered = filterMatchesByTeam(matches, query);
  const list = document.getElementById("results");

  const recent = filtered
    .filter(hasStarted)
    .sort((a, b) => new Date(b.utcDate) - new Date(a.utcDate));

  const upcoming = filtered
    .filter((m) => m.status === "TIMED" || m.status === "SCHEDULED")
    .sort((a, b) => new Date(a.utcDate) - new Date(b.utcDate))
    .slice(0, 5);

  if (recent.length === 0 && upcoming.length === 0) {
    list.innerHTML = matches.length === 0
      ? '<p class="empty-state">No match data yet.</p>'
      : '<p class="empty-state">No matches found for that search.</p>';
    return;
  }

  const section = (title, rows) =>
    rows.length === 0 ? "" : `<h2 class="results-section-heading">${title}</h2>${rows.map(renderResultRow).join("")}`;

  list.innerHTML = section("Recent Results", recent) + section("Upcoming", upcoming);
}

const BRACKET_STAGES = [
  { label: "Round of 32", codes: ["ROUND_OF_32", "LAST_32"] },
  { label: "Round of 16", codes: ["ROUND_OF_16", "LAST_16"] },
  { label: "Quarter-Finals", codes: ["QUARTER_FINALS"] },
  { label: "Semi-Finals", codes: ["SEMI_FINALS"] },
  { label: "Third Place", codes: ["THIRD_PLACE"] },
  { label: "Final", codes: ["FINAL"] },
];

function renderBracketMatch(m) {
  const started = hasStarted(m);
  const score = started ? `${m.homeScore ?? "-"} : ${m.awayScore ?? "-"}` : "vs";
  const homeWon = m.winner === "HOME_TEAM";
  const awayWon = m.winner === "AWAY_TEAM";

  return `
    <div class="bracket-match">
      <div class="bracket-team ${homeWon ? "winner" : ""}">${flagFor(m.homeTeam)} ${m.homeTeam}</div>
      <div class="bracket-score">${score}</div>
      <div class="bracket-team ${awayWon ? "winner" : ""}">${flagFor(m.awayTeam)} ${m.awayTeam}</div>
    </div>`;
}

function renderBracket(matches) {
  const container = document.getElementById("bracket");

  const columns = BRACKET_STAGES.map(({ label, codes }) => {
    const stageMatches = matches.filter((m) => codes.includes(m.stage));
    if (stageMatches.length === 0) return "";
    return `
      <div class="bracket-column">
        <h3 class="bracket-stage-label">${label}</h3>
        ${stageMatches.map(renderBracketMatch).join("")}
      </div>`;
  }).join("");

  if (!columns.trim()) {
    container.innerHTML =
      '<p class="empty-state">No knockout matches yet — check back once the group stage wraps up.</p>';
    return;
  }

  container.innerHTML = `<div class="bracket-columns">${columns}</div>`;
}

const TIMELINE_STAGES = [
  { key: "GROUP_STAGE", label: "Group" },
  { key: "ROUND_OF_32", label: "R32" },
  { key: "ROUND_OF_16", label: "R16" },
  { key: "QUARTER_FINALS", label: "QF" },
  { key: "SEMI_FINALS", label: "SF" },
  { key: "THIRD_PLACE", label: "3rd" },
  { key: "FINAL", label: "Final" },
];

function renderTimeline(stages) {
  const container = document.getElementById("timeline");

  const present = TIMELINE_STAGES.filter(({ key }) => stages && stages[key]);
  const totalMatches = present.reduce((sum, { key }) => sum + stages[key].total, 0);

  if (totalMatches === 0) {
    container.innerHTML = "";
    return;
  }

  const finishedMatches = present.reduce((sum, { key }) => sum + stages[key].finished, 0);
  const ballPct = Math.min(100, (finishedMatches / totalMatches) * 100);

  let cumulative = 0;
  const milestones = present
    .map(({ key, label }) => {
      const pct = (cumulative / totalMatches) * 100;
      cumulative += stages[key].total;
      const reached = pct <= ballPct;
      return `
        <div class="progress-milestone ${reached ? "reached" : ""}" style="left: ${pct}%">
          <span class="milestone-tick"></span>
          <span class="milestone-label">${label}</span>
        </div>`;
    })
    .join("");

  container.innerHTML = `
    <div class="progress-track">
      <div class="progress-rail">
        <div class="progress-fill" style="width: ${ballPct}%"></div>
        <div class="progress-ball" style="left: ${ballPct}%">⚽</div>
        ${milestones}
      </div>
    </div>`;
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
    const { lastUpdated, matches, stages } = await standingsRes.json();

    buildFlagLookup(players);
    renderLeaderboard(players, matches || []);
    renderResults(matches || []);
    renderBracket(matches || []);
    renderTimeline(stages);
    statusEl.textContent = formatTimestamp(lastUpdated);
  } catch (err) {
    statusEl.textContent = "Unable to load standings — showing last known state.";
    console.error(err);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh";
  }
}

const THEME_KEY = "wc-fantasy-theme";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.getElementById("theme-toggle").textContent = theme === "dark" ? "☀️" : "🌙";
}

function setupThemeToggle() {
  const stored = localStorage.getItem(THEME_KEY);
  applyTheme(stored || "light");

  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });
}

document.getElementById("refresh-btn").addEventListener("click", loadData);
document.getElementById("results-search").addEventListener("input", () => {
  renderResults(latestMatches);
});
setupTabs();
setupThemeToggle();
loadData();
