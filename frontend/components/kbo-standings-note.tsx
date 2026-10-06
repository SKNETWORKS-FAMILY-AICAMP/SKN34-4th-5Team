import type { KboSnapshot } from "@/lib/kbo/types";
import { standingsUpdatedAt } from "@/lib/kbo/fallback";

export function KboStandingsNote({ data }: { data: KboSnapshot }) {
  const meta = data.standingsMeta;
  if (!meta?.date || !data.standings.length) return null;
  return <div className="standings-footnote" role="status">
    {meta.isFallback && <p>요청한 날짜의 순위가 아직 준비되지 않아 마지막으로 확인된 순위를 표시합니다.</p>}
    <p>순위 기준일: <time dateTime={meta.date}>{meta.date}</time></p>
    <p>업데이트 시점: {standingsUpdatedAt(meta.updatedAt)}</p>
    <p>경기 결과와 순위가 반영되는 시점은 다를 수 있어요.</p>
  </div>;
}
