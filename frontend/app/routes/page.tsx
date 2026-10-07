"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Icon } from "@/components/icons";
import { RouteCard } from "@/components/route-card";
import { RouteBestSection } from "@/components/route-best-section";
import { RouteCardsSkeleton, RouteListSkeleton } from "@/components/route-skeleton";
import { retryRoutes, useRoutes, useRoutesError, useRoutesReady } from "@/lib/routes";
import { routeContentToText } from "@/lib/route-content";
import { getTeamBoardHref } from "@/lib/team-community";

const stadiums = ["전체", "잠실", "고척", "인천", "수원", "대전", "대구", "광주", "사직", "창원"];
const pageSize = 6;
type SearchField = "all" | "title" | "content" | "author";
const normalize = (value: string) => value.replace(/\s/g, "").toLocaleLowerCase("ko");

function RouteBrowseBoard({ initialQuery, initialStadium, deleted }: { initialQuery: string; initialStadium: string; deleted: boolean }) {
  const [query, setQuery] = useState(initialQuery);
  const [searchInput, setSearchInput] = useState(initialQuery);
  const [searchField, setSearchField] = useState<SearchField>("all");
  const [activeSearchField, setActiveSearchField] = useState<SearchField>("all");
  const [stadium, setStadium] = useState(initialStadium);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("likes");
  const [showDeleted, setShowDeleted] = useState(deleted);
  const resultsHeading = useRef<HTMLElement>(null);
  const routes = useRoutes();
  const ready = useRoutesReady();
  const loadError = useRoutesError();
  const filtered = routes.filter(route => {
    if (stadium !== "전체" && !normalize(route.stadium).includes(normalize(stadium))) return false;
    const content = routeContentToText(route.content, route.contentFormat);
    const fields = {
      all: [route.title, route.author, route.stadium, route.description, content, ...route.tags, ...route.stops.map(stop => stop.name)].join(" "),
      title: route.title,
      content: [route.description, content, ...route.stops.map(stop => stop.name)].join(" "),
      author: route.author,
    };
    return normalize(fields[activeSearchField]).includes(normalize(query));
  });
  const sorted = [...filtered].sort((a, b) => {
    const newest = b.createdAt.localeCompare(a.createdAt);
    if (sort === "likes") return b.likes - a.likes || newest || b.id.localeCompare(a.id);
    if (sort === "views") return (b.views ?? 0) - (a.views ?? 0) || newest;
    return newest;
  });
  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visible = sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const firstPage = Math.max(1, Math.min(currentPage - 2, pageCount - 4));
  const pageNumbers = Array.from({ length: Math.min(5, pageCount) }, (_, index) => firstPage + index);
  const writeHref = stadium === "전체" ? "/routes/new" : `/routes/new?stadium=${encodeURIComponent(stadium)}`;

  function focusResults() {
    resultsHeading.current?.focus({ preventScroll: true });
    resultsHeading.current?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
  function changePage(nextPage: number) { setPage(nextPage); focusResults(); }
  function resetFilters() {
    setQuery(""); setSearchInput(""); setSearchField("all"); setActiveSearchField("all"); setStadium("전체"); setPage(1);
  }

  return (
    <main className="container community-page header-aligned-content">
      <nav className="community-breadcrumb" aria-label="현재 위치"><Link href="/">홈</Link><Icon name="chevron" size={12}/><span aria-current="page">코스 둘러보기</span></nav>
      <header className="community-header">
        <div><p className="eyebrow">KBO ROUTES</p><h1>직관 코스 둘러보기</h1><p className="community-description">다른 팬들의 코스를 만나봐요</p></div>
        <Link href={writeHref} className="button button-primary community-write"><Icon name="book" size={18}/>코스 만들기</Link>
      </header>

      <section className="community-board" aria-label="직관 코스 목록" ref={resultsHeading} tabIndex={-1}>
          {showDeleted && <div className="route-feedback" role="status"><span>코스를 삭제했어요.</span><button type="button" aria-label="삭제 안내 닫기" onClick={() => setShowDeleted(false)}>×</button></div>}
          {loadError && <div className="route-error" role="alert"><span>{loadError} 이전 버전의 브라우저 코스만 표시될 수 있어요.</span> <button type="button" onClick={() => void retryRoutes()}>다시 불러오기</button></div>}

          <div className="community-stadiums" role="group" aria-label="구장으로 필터">
            {stadiums.map(item => <button type="button" key={item} className={stadium === item ? "is-active" : ""} aria-pressed={stadium === item} onClick={() => { setStadium(item); setPage(1); }}>{item}</button>)}
          </div>
          <RouteBestSection stadium={stadium} routes={routes} />
          <div className="community-toolbar">
            <p>전체 <strong>{ready ? sorted.length : "…"}</strong>개 <span className="community-page-count">{currentPage} / {pageCount} 페이지</span></p>
            <div className="community-sort">
              <label><span className="sr-only">코스 정렬</span><select value={sort} onChange={event => { setSort(event.target.value); setPage(1); }}><option value="newest">최신순</option><option value="likes">좋아요순</option><option value="views">조회순</option></select></label>
            </div>
          </div>
          {query && <div className="community-search-result"><span>‘{query}’ 검색 결과</span><button type="button" onClick={() => { setQuery(""); setSearchInput(""); setPage(1); }}>검색 해제 <Icon name="close" size={13}/></button></div>}
          <p className="sr-only" role="status">{ready ? `코스 ${sorted.length}개, ${currentPage}페이지` : "코스를 불러오고 있어요."}</p>

          {!ready ? <RouteCardsSkeleton count={pageSize} className="route-card-grid route-browse-grid" /> : visible.length ?
            <div className="route-card-grid route-browse-grid" aria-label="직관 코스 카드 목록">
              {visible.map(route => <RouteCard key={route.id} route={route} />)}
            </div> :
            <div className="route-browse-empty"><Icon name="search" size={32}/><h2>{query ? "검색 결과가 없어요" : "아직 등록된 코스가 없어요"}</h2><p>{query ? "다른 검색어를 입력하거나 구장 필터를 바꿔보세요." : `${stadium === "전체" ? "나만의" : stadium} 직관 코스를 첫 글로 남겨보세요.`}</p><div><button type="button" className="button button-secondary" onClick={resetFilters}>전체 코스 보기</button><Link href={writeHref} className="button button-primary">코스 만들기</Link></div></div>}

          <div className="community-board-bottom">
            {ready && sorted.length > 0 && <nav className="route-pagination community-pagination" aria-label="직관 코스 페이지">
              <button type="button" aria-label="처음 페이지" disabled={currentPage === 1} onClick={() => changePage(1)}>«</button>
              <button type="button" aria-label="이전 페이지" disabled={currentPage === 1} onClick={() => changePage(currentPage - 1)}>‹</button>
              {pageNumbers.map(number => <button type="button" key={number} aria-label={`${number}페이지`} aria-current={currentPage === number ? "page" : undefined} className={currentPage === number ? "is-active" : ""} onClick={() => changePage(number)}>{number}</button>)}
              <button type="button" aria-label="다음 페이지" disabled={currentPage === pageCount} onClick={() => changePage(currentPage + 1)}>›</button>
              <button type="button" aria-label="마지막 페이지" disabled={currentPage === pageCount} onClick={() => changePage(pageCount)}>»</button>
            </nav>}
          </div>
          <form className="community-search" role="search" aria-label="직관 코스 검색" onSubmit={event => { event.preventDefault(); setQuery(searchInput.trim()); setActiveSearchField(searchField); setPage(1); focusResults(); }}>
            <label><span className="sr-only">검색 범위</span><select value={searchField} onChange={event => setSearchField(event.target.value as SearchField)}><option value="all">전체</option><option value="title">제목</option><option value="content">내용</option><option value="author">글쓴이</option></select></label>
            <label className="community-search-input"><span className="sr-only">코스 검색어</span><input type="search" placeholder="궁금한 직관 코스를 검색해보세요" maxLength={150} value={searchInput} onChange={event => setSearchInput(event.target.value)}/></label>
            <button type="submit" aria-label="코스 검색"><Icon name="search" size={18}/><span>검색</span></button>
          </form>
          <p className="community-storage-note">새 코스는 공개 저장되고 편집 권한·좋아요·조회 수만 이 브라우저에 저장됩니다. 이전 버전 코스는 다시 저장하기 전까지 이 브라우저에만 남습니다.</p>
      </section>
    </main>
  );
}

function RoutesQuery() {
  const params = useSearchParams();
  if (params.get("board") === "free") return <LegacyTeamBoardRedirect teamCode={params.get("team") ?? ""} postId={params.get("post") ?? ""}/>;
  const requestedStadium = params.get("stadium") ?? "전체";
  const initialStadium = stadiums.find(stadium => normalize(requestedStadium).includes(stadium)) ?? "전체";
  return <RouteBrowseBoard key={params.toString()} initialQuery={params.get("q") ?? ""} initialStadium={initialStadium} deleted={params.get("deleted") === "1"}/>;
}

function LegacyTeamBoardRedirect({ teamCode, postId }: { teamCode: string; postId: string }) {
  const router = useRouter();
  const href = getTeamBoardHref(teamCode, postId);
  useEffect(() => { router.replace(href); }, [href, router]);
  return <main className="container community-page header-aligned-content"><p role="status">팀 게시판으로 이동하고 있어요.</p></main>;
}

export default function RoutesPage() {
  return <Suspense fallback={<RouteListSkeleton/>}><RoutesQuery/></Suspense>;
}
