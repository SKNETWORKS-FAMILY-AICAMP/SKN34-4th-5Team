"use client";

import { useEffect, useState } from "react";
import { fetchBestCourses } from "@/lib/course-api";
import type { TripRoute } from "@/lib/routes";
import { RouteCard } from "./route-card";
import { RouteCardsSkeleton } from "./route-skeleton";
import styles from "./route-best-section.module.css";

const codes: Record<string, string> = {
  잠실: "JAMSIL", 고척: "GOCHEOK", 인천: "MUNHAK", 수원: "SUWON", 대전: "DAEJEON",
  대구: "DAEGU", 광주: "GWANGJU", 사직: "SAJIK", 창원: "CHANGWON",
};

export function RouteBestSection({ stadium, routes }: { stadium: string; routes: TripRoute[] }) {
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ source: TripRoute[]; stadium: string; retry: number; posts: TripRoute[]; error: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchBestCourses(codes[stadium] ?? "", controller.signal).then(posts => {
      if (!controller.signal.aborted) setResult({ source: routes, stadium, retry, posts, error: "" });
    }, () => {
      if (!controller.signal.aborted) setResult({ source: routes, stadium, retry, posts: [], error: "추천 코스를 불러오지 못했어요." });
    });
    return () => controller.abort();
  }, [stadium, routes, retry]);
  const current = result?.source === routes && result.stadium === stadium && result.retry === retry ? result : null;
  return <section className={styles.section} aria-labelledby="route-best-heading" aria-busy={!current}>
    <h2 id="route-best-heading">{stadium === "전체" ? "전체 구장" : `${stadium} 구장`} BEST 5</h2>
    <p className={styles.description}>등록된 코스를 좋아요순으로 최대 5개 보여드려요. 예시 코스는 제외하며, 동률이면 최신 코스가 먼저 표시돼요.</p>
    {!current ? <RouteCardsSkeleton count={5} className={styles.grid} /> : current.error ?
      <div role="alert"><p>{current.error}</p><button type="button" onClick={() => setRetry(value => value + 1)}>다시 불러오기</button></div> :
      current.posts.length ? <ol className={styles.grid}>{current.posts.map((route, index) =>
        <li key={route.id} className={styles.item}><span className={styles.rank}>{index + 1}위</span><RouteCard route={route} /></li>
      )}</ol> : <p role="status">아직 이 구장에 등록된 추천 대상 코스가 없어요.</p>}
  </section>;
}
