import type { components } from "@/lib/api/schema";

export type KboGameStatus = "scheduled" | "live" | "final" | "cancelled" | "postponed" | "suspended" | "unknown";

export type KboGame = {
  id: string;
  date: string;
  startsAt: string | null;
  time: string;
  stadium: string;
  away: { code: string; name: string; score: number | null; startingPitcher?: string | null };
  home: { code: string; name: string; score: number | null; startingPitcher?: string | null };
  status: KboGameStatus;
  statusLabel: string;
};

export type KboStanding = {
  rank: number;
  teamCode: string;
  team: string;
  played: number;
  wins: number;
  draws: number;
  losses: number;
  winRate: string;
  gamesBehind: string;
  streak: string;
  battingAverage: string;
  era: string;
  lastTen: string;
};

export type KboAthleteRankingBase = {
  rank: number;
  playerCode: string;
  player: string;
  teamCode: string;
  team: string;
};

export type KboPitcherRanking = KboAthleteRankingBase & {
  earnedRunAverage: string;
  fip: string;
  whip: string;
  war: string;
  qualityStarts: string;
  games: string;
  wins: string;
  losses: string;
  saves: string;
  holds: string;
  innings: string;
  strikeouts: string;
  hitsAllowed: string;
  homeRunsAllowed: string;
  walks: string;
  hitByPitch: string;
  wildPitches: string;
  runsAllowed: string;
  winningPercentage: string;
};

export type KboHitterRanking = KboAthleteRankingBase & {
  battingAverage: string;
  ops: string;
  wrcPlus: string;
  war: string;
  games: string;
  atBats: string;
  hits: string;
  doubles: string;
  triples: string;
  homeRuns: string;
  runsBattedIn: string;
  runs: string;
  stolenBases: string;
  walks: string;
  strikeouts: string;
  doublePlays: string;
  onBasePercentage: string;
  sluggingPercentage: string;
};

export type KboIndividualRankings = {
  pitchers: KboPitcherRanking[];
  hitters: KboHitterRanking[];
};

export type KboSourceData = {
  date: string;
  games: KboGame[];
  standings: KboStanding[];
  // Optional so a cache created by the previous app version remains readable.
  // The collector immediately refreshes such a cache and fills this field.
  individualRankings?: KboIndividualRankings;
  sourceUpdatedAt: string | null;
};

export type KboSnapshot = KboSourceData & {
  scheduleStatus?: components["schemas"]["DailyData"]["scheduleStatus"];
  standingsMeta?: components["schemas"]["StandingsMetadata"];
  individualRankingsStatus?: components["schemas"]["RankingStatus"];
  fetchedAt: string | null;
  updatedAt: string | null;
  nextCheckAt: string | null;
  mode: "hourly" | "five-minute" | "final-check" | "fixed-interval";
  source: { name: string; url: string };
  stale: boolean;
  warning: string | null;
};

export type KboApiResponse = { data: KboSnapshot | null; error: string | null };

export type KboScheduleDay = {
  date: string;
  status: "ready" | "empty" | "pending" | "error";
  gameCount: number;
};

export type KboScheduleMonth = {
  year: 2026;
  month: string;
  today: string;
  games: KboGame[];
  days: KboScheduleDay[];
  fetchedAt: string | null;
  stale: boolean;
  warning: string | null;
  loading: boolean;
  source: { name: string; url: string };
};

export type KboScheduleResponse = { data: KboScheduleMonth | null; error: string | null };
