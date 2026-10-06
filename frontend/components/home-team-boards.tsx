"use client";

import Link from "next/link";
import { PostCategory } from "./post-category";
import { PostCommentCount } from "./post-comment-count";
import { useState } from "react";
import type { KboStanding } from "@/lib/kbo/types";
import { getTeamBoard, getTeamBoardHref, teamBoards } from "@/lib/team-community";
import { retryCommunityPosts, useCommunityPosts } from "@/lib/community-api";
import { Icon } from "./icons";
import { TeamLogo } from "./team-logo";

export function HomeTeamBoards({ standings, retry }: {
  standings: KboStanding[] | null;
  loading: boolean;
  retry: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const community = useCommunityPosts();
  const teams = standings?.length ? [...standings].sort((a, b) => a.rank - b.rank).flatMap(standing => {
    const team = getTeamBoard(standing.teamCode);
    return team ? [{ ...team, rank: standing.rank }] : [];
  }) : teamBoards.map(team => ({ ...team, rank: null }));
  const visible = expanded ? teams : teams.slice(0, 6);

  return (
    <section className="container home-community-section" aria-labelledby="home-community-heading">
      <div className="section-heading">
        <div><span className="eyebrow">FAN COMMUNITY</span><h2 id="home-community-heading">팀 게시판</h2><p>같은 팀을 응원하는 우리, 야구 이야기를 나눠요.</p></div>
        <Link href={getTeamBoardHref()} className="text-link">게시판 이동 <Icon name="chevron" size={17} /></Link>
      </div>
      <div className="home-community-meta"><span>최신 게시글</span><p>{standings?.length ? "위 순위표 순서로 만나는 팀별 이야기" : "순위 미확인 · 기본 구단 순서로 표시합니다."}</p></div>
      {community.error && community.posts.length > 0 && <p role="alert">{community.error} <button type="button" className="text-link" onClick={() => void retryCommunityPosts()}>다시 확인</button></p>}
      {community.loading && community.posts.length === 0 ? (
        <div className="home-community-grid home-community-loading" role="status">
          <span className="sr-only">팀 순위에 맞춰 게시판을 불러오고 있어요.</span>
          {Array.from({ length: 6 }, (_, index) => <div className="home-team-board" key={index} aria-hidden="true"><div className="route-skeleton home-board-skeleton-title" />{Array.from({ length: 5 }, (_, row) => <div key={row} className="route-skeleton home-board-skeleton-row" />)}</div>)}
        </div>
      ) : community.error && community.posts.length === 0 ? (
        <div className="home-community-empty" role="alert"><p>{community.error}</p><button type="button" className="text-link" onClick={() => void retryCommunityPosts()}>다시 확인 <Icon name="arrow" size={15} /></button></div>
      ) : teams.length ? (
        <>
          <div className="home-community-grid" id="home-team-boards">
            {visible.map(team => (
              <article className="home-team-board" key={team.code} aria-labelledby={`home-board-${team.code}`}>
                <Link className="home-board-heading" href={getTeamBoardHref(team.code)}>
                  <TeamLogo code={team.code} name={team.name} className="home-board-logo" />
                  <div><span className="home-board-rank">{team.rank ? `${team.rank}위 · ` : ""}<span>팀 게시판</span></span><h3 id={`home-board-${team.code}`}>{team.name}</h3></div>
                  <Icon name="chevron" size={16} />
                </Link>
                <ul className="home-board-posts">
                  {community.posts.filter(post => post.board === "teams" && post.teamCode === team.code).slice(0, 5).map(post => <li key={post.id}><Link href={getTeamBoardHref(team.code, post.id)}><PostCategory category={post.category} /><span className="home-board-post-title">{post.title}</span><PostCommentCount count={post.commentCount} /></Link></li>)}
                </ul>
              </article>
            ))}
          </div>
          {teams.length > 6 && <div className="home-community-more-wrap"><button type="button" className="home-community-more" aria-expanded={expanded} aria-controls="home-team-boards" onClick={() => setExpanded(value => !value)}><span>{expanded ? "접기" : "MORE"}</span>{!expanded && <small>나머지 {teams.length - 6}팀</small>}<Icon name="chevron" size={16} /></button></div>}
        </>
      ) : (
        <div className="home-community-empty" role="status"><p>순위를 확인하면 팀별 게시판을 보여드릴게요.</p><button type="button" className="text-link" onClick={retry}>다시 확인 <Icon name="arrow" size={15} /></button></div>
      )}
    </section>
  );
}
