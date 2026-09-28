import { notFound } from "next/navigation";
import { CommunityMemberPage } from "@/components/community-member-page";

export const metadata = { title: "멤버 활동", robots: { index: false, follow: false } };

export default async function Page({ params, searchParams }: {
  params: Promise<{ memberId: string }>;
  searchParams: Promise<{ tab?: string | string[]; page?: string | string[] }>;
}) {
  const [route, query] = await Promise.all([params, searchParams]);
  const memberId = Number(route.memberId);
  if (!/^[1-9]\d*$/.test(route.memberId) || !Number.isSafeInteger(memberId)) notFound();
  const tab = query.tab === "comments" ? "comments" : "posts";
  const rawPage = typeof query.page === "string" ? query.page : "1";
  const parsed = Number(rawPage);
  const page = /^[1-9]\d*$/.test(rawPage) && Number.isSafeInteger(parsed) && parsed <= 2_147_483_647 ? parsed : 1;
  return <CommunityMemberPage memberId={memberId} tab={tab} page={page} />;
}
