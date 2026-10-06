import type { Metadata } from "next";
import { KboSchedulePage } from "@/components/kbo-schedule-page";
import { supportedScheduleDate } from "@/lib/kbo/fallback";
import "@/styles/kbo-detail.css";

export const metadata: Metadata = { title: "KBO 경기 일정" };

export default async function SchedulePage({ searchParams }: {
  searchParams: Promise<{ date?: string | string[] }>;
}) {
  const { date } = await searchParams;
  return <KboSchedulePage initialDate={supportedScheduleDate(date) ? date : undefined}
    invalidDate={date !== undefined && !supportedScheduleDate(date)} />;
}
