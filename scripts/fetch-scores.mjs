// Run by .github/workflows/update-scores.yml on a schedule.
// Pulls World Cup matches from football-data.org, keeps only matches
// involving one of our tracked teams, and writes data/standings.json.
import { readFile, writeFile } from "node:fs/promises";

const API_KEY = process.env.FOOTBALL_DATA_API_KEY;
if (!API_KEY) {
  console.error("Missing FOOTBALL_DATA_API_KEY env var");
  process.exit(1);
}

const normalize = (s) =>
  s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9 ]/g, "")
    .trim();

const { players } = JSON.parse(
  await readFile(new URL("../config/players.json", import.meta.url))
);
const trackedAliases = players.flatMap((p) =>
  p.teams.flatMap((t) => t.aliases.map(normalize))
);

const isTracked = (teamName) => {
  const n = normalize(teamName || "");
  return trackedAliases.some((alias) => alias === n);
};

const res = await fetch("https://api.football-data.org/v4/competitions/WC/matches", {
  headers: { "X-Auth-Token": API_KEY },
});

if (!res.ok) {
  console.error(`football-data.org request failed: ${res.status} ${res.statusText}`);
  process.exit(1);
}

const body = await res.json();
const allMatches = body.matches || [];

const matches = allMatches
  .filter((m) => isTracked(m.homeTeam?.name) || isTracked(m.awayTeam?.name))
  .map((m) => ({
    stage: m.stage,
    status: m.status,
    utcDate: m.utcDate,
    homeTeam: m.homeTeam?.name ?? "TBD",
    awayTeam: m.awayTeam?.name ?? "TBD",
    homeScore: m.score?.fullTime?.home ?? null,
    awayScore: m.score?.fullTime?.away ?? null,
    winner: m.score?.winner ?? null,
  }));

// Canonical stage buckets, computed from the *full* (unfiltered) match
// list so knockout-round dates are known even before any tracked team
// has been slotted into a knockout fixture (those entries have TBD
// teams but a real scheduled stage + utcDate).
const STAGE_GROUPS = {
  GROUP_STAGE: "GROUP_STAGE",
  ROUND_OF_32: "ROUND_OF_32",
  LAST_32: "ROUND_OF_32",
  ROUND_OF_16: "ROUND_OF_16",
  LAST_16: "ROUND_OF_16",
  QUARTER_FINALS: "QUARTER_FINALS",
  SEMI_FINALS: "SEMI_FINALS",
  THIRD_PLACE: "THIRD_PLACE",
  FINAL: "FINAL",
};

const stages = {};
for (const m of allMatches) {
  const canon = STAGE_GROUPS[m.stage];
  if (!canon || !m.utcDate) continue;
  if (!stages[canon]) {
    stages[canon] = { total: 0, finished: 0, startDate: m.utcDate, endDate: m.utcDate };
  }
  const s = stages[canon];
  s.total += 1;
  if (m.status === "FINISHED") s.finished += 1;
  if (m.utcDate < s.startDate) s.startDate = m.utcDate;
  if (m.utcDate > s.endDate) s.endDate = m.utcDate;
}

const output = {
  lastUpdated: new Date().toISOString(),
  matches,
  stages,
};

await writeFile(
  new URL("../data/standings.json", import.meta.url),
  JSON.stringify(output, null, 2) + "\n"
);

console.log(`Wrote ${matches.length} tracked matches.`);
