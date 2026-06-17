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
  return trackedAliases.some((alias) => n.includes(alias) || alias.includes(n));
};

const res = await fetch("https://api.football-data.org/v4/competitions/WC/matches", {
  headers: { "X-Auth-Token": API_KEY },
});

if (!res.ok) {
  console.error(`football-data.org request failed: ${res.status} ${res.statusText}`);
  process.exit(1);
}

const body = await res.json();

const matches = (body.matches || [])
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

const output = {
  lastUpdated: new Date().toISOString(),
  matches,
};

await writeFile(
  new URL("../data/standings.json", import.meta.url),
  JSON.stringify(output, null, 2) + "\n"
);

console.log(`Wrote ${matches.length} tracked matches.`);
