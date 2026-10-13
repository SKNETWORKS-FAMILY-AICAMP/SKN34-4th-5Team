import type { KboHighlight } from "./types";

const KBO_CHANNEL_TITLE = "KBO";
const KBO_UPLOADS_PLAYLIST = "UUoVz66yWHzVsXAFG8WhJK9g";
const YOUTUBE_API = "https://www.googleapis.com/youtube/v3/playlistItems";
const YOUTUBE_VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos";
const CACHE_MS = 10 * 60 * 1000;
const MAX_PAGES = 5;
const DEFAULT_HIGHLIGHT_COUNT = 3;
const MAX_HIGHLIGHT_COUNT = 12;

type PlaylistItem = {
  snippet?: {
    title?: unknown;
    videoOwnerChannelTitle?: unknown;
    thumbnails?: Record<string, { url?: unknown }>;
  };
  contentDetails?: { videoId?: unknown; videoPublishedAt?: unknown };
};

type PlaylistResponse = {
  items?: unknown;
  nextPageToken?: unknown;
  error?: { errors?: Array<{ reason?: unknown }> };
};

type VideoResponse = {
  items?: Array<{
    id?: unknown;
    contentDetails?: { duration?: unknown };
    statistics?: { viewCount?: unknown };
  }>;
  error?: { errors?: Array<{ reason?: unknown }> };
};

type HighlightRuntime = {
  value: KboHighlight[];
  updatedAt: number;
  pending: Promise<KboHighlight[]> | null;
};

declare global {
  var __kboHighlightRuntimeV3: HighlightRuntime | undefined;
}

const runtime = globalThis.__kboHighlightRuntimeV3 ??= { value: [], updatedAt: 0, pending: null };

export function isKboLeagueHighlightTitle(title: string) {
  return /^\[[^\]]+\s+vs\s+[^\]]+\]/i.test(title)
    && title.includes("야구 하이라이트")
    // 정규시즌 "2026 KBO 리그"와 가을야구 "2026 신한 SOL KBO 포스트시즌"(타이틀 스폰서 포함)을 함께 받는다.
    && /\d{4}\s*(?:신한\s*SOL\s*(?:뱅크\s*)?)?KBO\s*(?:리그|포스트시즌|와일드카드|준플레이오프|플레이오프|한국시리즈)/i.test(title)
    && /KBO\s*X\s*TVING/i.test(title)
    && !title.includes("퓨처스리그");
}

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function parseHighlight(item: PlaylistItem): Omit<KboHighlight, "duration" | "viewCount" | "fetchedAt" | "stale"> | null {
  const videoId = text(item.contentDetails?.videoId);
  const title = text(item.snippet?.title);
  const publishedAt = text(item.contentDetails?.videoPublishedAt);
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId) || !isKboLeagueHighlightTitle(title) || Number.isNaN(Date.parse(publishedAt))) return null;
  const thumbnails = item.snippet?.thumbnails;
  const thumbnailUrl = text(thumbnails?.maxres?.url)
    || text(thumbnails?.standard?.url)
    || text(thumbnails?.high?.url)
    || `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;
  return {
    videoId,
    title,
    publishedAt,
    channelTitle: text(item.snippet?.videoOwnerChannelTitle) || KBO_CHANNEL_TITLE,
    watchUrl: `https://www.youtube.com/watch?v=${videoId}`,
    thumbnailUrl,
  };
}

function formatDuration(value: string) {
  const match = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(value);
  if (!match) return null;
  const hours = Number(match[1] || 0);
  const minutes = Number(match[2] || 0);
  const seconds = Number(match[3] || 0);
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

async function requestVideoDetails(key: string, videoIds: string[]) {
  const url = new URL(YOUTUBE_VIDEOS_API);
  url.searchParams.set("part", "contentDetails,statistics");
  url.searchParams.set("id", videoIds.join(","));
  url.searchParams.set("key", key);
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(12_000) });
  const body = await response.json() as VideoResponse;
  if (!response.ok) {
    const reason = text(body.error?.errors?.[0]?.reason);
    throw new Error(reason || `YouTube API ${response.status}`);
  }
  return new Map((body.items ?? []).map(item => {
    const rawViews = text(item.statistics?.viewCount);
    const parsedViews = Number(rawViews);
    return [text(item.id), {
      duration: formatDuration(text(item.contentDetails?.duration)),
      viewCount: Number.isSafeInteger(parsedViews) && parsedViews >= 0 ? parsedViews : null,
    }] as const;
  }));
}

async function requestPage(key: string, pageToken?: string) {
  const url = new URL(YOUTUBE_API);
  url.searchParams.set("part", "snippet,contentDetails");
  url.searchParams.set("playlistId", KBO_UPLOADS_PLAYLIST);
  url.searchParams.set("maxResults", "50");
  url.searchParams.set("key", key);
  if (pageToken) url.searchParams.set("pageToken", pageToken);

  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(12_000) });
  const body = await response.json() as PlaylistResponse;
  if (!response.ok) {
    const reason = text(body.error?.errors?.[0]?.reason);
    throw new Error(reason || `YouTube API ${response.status}`);
  }
  return body;
}

async function collectLatestHighlights(): Promise<KboHighlight[]> {
  const key = process.env.YOUTUBE_API_KEY?.trim();
  if (!key) throw new Error("YOUTUBE_API_KEY is not configured");

  let pageToken: string | undefined;
  const highlights: Array<Omit<KboHighlight, "duration" | "viewCount" | "fetchedAt" | "stale">> = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const body = await requestPage(key, pageToken);
    const items = Array.isArray(body.items) ? body.items as PlaylistItem[] : [];
    highlights.push(...items.map(parseHighlight).filter((item): item is NonNullable<typeof item> => Boolean(item)));
    if (highlights.length >= MAX_HIGHLIGHT_COUNT) break;
    pageToken = text(body.nextPageToken) || undefined;
    if (!pageToken) break;
  }
  if (!highlights.length) throw new Error("KBO league highlights were not found");
  const selected = highlights.slice(0, MAX_HIGHLIGHT_COUNT);
  const details = await requestVideoDetails(key, selected.map(item => item.videoId)).catch(() => new Map<string, { duration: string | null; viewCount: number | null }>());
  const fetchedAt = new Date().toISOString();
  return selected.map(item => ({
    ...item,
    duration: details.get(item.videoId)?.duration ?? null,
    viewCount: details.get(item.videoId)?.viewCount ?? null,
    fetchedAt,
    stale: false,
  }));
}

export async function getLatestKboHighlights(limit = DEFAULT_HIGHLIGHT_COUNT): Promise<KboHighlight[]> {
  const normalizedLimit = Number.isSafeInteger(limit) ? Math.min(MAX_HIGHLIGHT_COUNT, Math.max(1, limit)) : DEFAULT_HIGHLIGHT_COUNT;
  if (runtime.value.length >= normalizedLimit && Date.now() - runtime.updatedAt < CACHE_MS) return runtime.value.slice(0, normalizedLimit);
  if (runtime.pending) return (await runtime.pending).slice(0, normalizedLimit);

  runtime.pending = collectLatestHighlights()
    .then((value) => {
      runtime.value = value;
      runtime.updatedAt = Date.now();
      return value;
    })
    .catch((error) => {
      if (runtime.value.length) return runtime.value.map(value => ({ ...value, stale: true }));
      throw error;
    })
    .finally(() => { runtime.pending = null; });
  return (await runtime.pending).slice(0, normalizedLimit);
}
