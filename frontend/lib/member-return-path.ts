const LOCAL_ORIGIN = "https://local.invalid";

export function safeMemberReturnPath(raw: string | null): string | null {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return null;
  try {
    const url = new URL(raw, LOCAL_ORIGIN);
    if (url.origin !== LOCAL_ORIGIN) return null;
    const match = /^\/community\/members\/([1-9]\d*)\/?$/.exec(url.pathname);
    if (!match || !Number.isSafeInteger(Number(match[1]))) return null;
    const tab = url.searchParams.get("tab") === "comments" ? "comments" : "posts";
    const rawPage = url.searchParams.get("page") ?? "1";
    const parsed = Number(rawPage);
    const page = /^[1-9]\d*$/.test(rawPage) && Number.isSafeInteger(parsed) && parsed <= 2_147_483_647 ? parsed : 1;
    return `/community/members/${Number(match[1])}?${new URLSearchParams({ tab, page: String(page) })}`;
  } catch {
    return null;
  }
}
