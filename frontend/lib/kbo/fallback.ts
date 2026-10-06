export const MIN_SCHEDULE_DATE = "2026-01-01";
export const MAX_SCHEDULE_DATE = "2026-12-31";

export function supportedScheduleDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^2026-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + "T12:00:00Z");
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function nextCalendarDate(date: string) {
  const value = new Date(date + "T12:00:00Z");
  value.setUTCDate(value.getUTCDate() + 1);
  return value.toISOString().slice(0, 10);
}

export function standingsUpdatedAt(value: string | null) {
  if (!value || Number.isNaN(new Date(value).getTime())) return "확인되지 않음";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value)) + " (한국시간)";
}
