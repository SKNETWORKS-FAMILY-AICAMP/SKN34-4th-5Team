"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { fetchMyCommunityPosts } from "@/lib/community-api";
import { useMemberAuth } from "@/lib/member-auth";
import { getTeamBoardHref, type TeamCommunityPost } from "@/lib/team-community";
import styles from "./community-board.module.css";

const areas = [{ id: "all", name: "전체" }, { id: "free", name: "자유게시판" }, { id: "teams", name: "팀 게시판" }] as const;

export function MemberPosts() {
  const { user } = useMemberAuth();
  return user ? <MemberPostsContent key={user.id} /> : <p className={styles.commentsEmpty}>로그인 후 이용할 수 있어요.</p>;
}

function MemberPostsContent() {
  const request = useRef(0);
  const [retry, setRetry] = useState(0);
  const [area, setArea] = useState<string>("all");
  const [state, setState] = useState<{ posts: TeamCommunityPost[]; loading: boolean; error: string }>({ posts: [], loading: true, error: "" });

  useEffect(() => {
    const current = ++request.current;
    void fetchMyCommunityPosts().then(posts => {
      if (request.current === current) setState({ posts, loading: false, error: "" });
    }).catch(cause => {
      if (request.current === current) setState({ posts: [], loading: false, error: cause instanceof Error ? cause.message : "내 게시글을 불러오지 못했어요." });
    });
    return () => { request.current += 1; };
  }, [retry]);

  const visible = state.posts.filter(post => area === "all" || post.board === area);
  const href = (post: TeamCommunityPost) => post.moderationStatus !== "approved" ? `/community/submissions?post=${encodeURIComponent(post.id)}` : post.board === "free" ? `/community?post=${encodeURIComponent(post.id)}` : getTeamBoardHref(post.teamCode, post.id);
  return <section aria-label="내가 쓴 글">
    <h2 className={styles.memberPostsHeading}>내가 쓴 글</h2>
    <div className={styles.memberPostTags} role="group" aria-label="작성 영역 선택">{areas.map(item => <button type="button" key={item.id} aria-pressed={area === item.id} onClick={() => setArea(item.id)}>{item.name}</button>)}</div>
    {state.loading ? <p role="status" className={styles.commentsEmpty}>내 게시글을 불러오고 있어요.</p> : state.error ? <p role="alert" className={styles.commentsEmpty}>{state.error} <button type="button" onClick={() => { setState({ posts: [], loading: true, error: "" }); setRetry(value => value + 1); }}>다시 시도</button></p> : visible.length ? <ul className={styles.memberPostList}>{visible.map(post => <li key={post.id}><span>{post.board === "free" ? "자유게시판" : "팀 게시판"}</span><Link href={href(post)}>{post.title}</Link>{post.createdAt ? <time dateTime={post.createdAt}>{new Date(post.createdAt).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" })}</time> : <span>—</span>}</li>)}</ul> : <p className={styles.commentsEmpty}>이 영역에 작성한 글이 없어요.</p>}
  </section>;
}
