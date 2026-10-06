import { stadiums as presentation } from "@/lib/stadiums";
import type { Stadium } from "@/lib/stadiums";
import type { BaseballStadium } from "./types";

export function adaptStadium(row: BaseballStadium): Stadium | null {
  const visual = presentation.find((item) => item.code === row.stadium_code);
  const lng = Number(row.longitude), lat = Number(row.latitude);
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || Math.abs(lng) > 180 || Math.abs(lat) > 90) return null;
  return {
    code: row.stadium_code, name: row.stadium_name_ko, address: row.address,
    lng, lat, region: visual?.region ?? "미분류",
    teams: [...new Set(row.home_teams.map((team) => team.name))], color: visual?.color ?? "blue",
    seatingMap: visual?.seatingMap ?? { src: "/images/stadium-day.jpg", sourceUrl: "" },
    cardImage: visual?.cardImage ?? { src: "/images/stadium-day.jpg", sourceUrl: "", credit: "프로젝트 기본 이미지", creditUrl: "/images/SOURCES.md" },
  };
}
