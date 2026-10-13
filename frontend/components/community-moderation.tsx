"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { memberFetch, memberError } from "@/lib/member-auth-request";
import { fetchMyCommunityPosts, fetchMyCommunityComments, deleteCommunityPost, deleteCommunityComment, updateCommunityComment, type CommunityComment } from "@/lib/community-api";
import { useMemberAuth } from "@/lib/member-auth";
import type { TeamCommunityPost } from "@/lib/team-community";
import { getTeamBoardHref } from "@/lib/team-community";

export const moderationLabel: Record<string, string> = { pending: "비공개 · 검사 대기", review: "비공개 · 운영진 검토", error: "비공개 · 검사 오류", rejected: "비공개 · 보류 확정", approved: "검사 승인" };

export function AdminContentPreview({ id }: { id: string }) {
  const [post, setPost] = useState<TeamCommunityPost | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void memberFetch(`/api/v1/community/admin/posts/${encodeURIComponent(id)}/`, { cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error("게시글을 불러오지 못했어요.");
      const value = await response.json();
      if (active) setPost(value);
    }).catch(() => { if (active) setError("게시글을 불러오지 못했어요."); });
    return () => { active = false; };
  }, [id]);
  return <section aria-label="운영진 원문 열람"><Link href="/admin?tab=posts">원문 닫기</Link>{error && <p role="alert">{error}</p>}{post && <><h2>{post.title}</h2><p>{moderationLabel[post.moderationStatus]} · {post.revision}차</p><p style={{ whiteSpace: "pre-wrap" }}>{post.content}</p>{post.images?.map(image => <PrivateCommunityImage key={image.id} id={image.id} />)}</>}</section>;
}

export function PrivateCommunityImage({ id }: { id: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void memberFetch(`/api/v1/community/images/${encodeURIComponent(id)}/`, { cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error("image unavailable");
      const blob = await response.blob();
      if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id]);
  return error ? <p role="alert">첨부 이미지를 불러오지 못했어요.</p> : url ? <img src={url} alt="비공개 첨부 이미지" style={{ maxWidth: "100%", maxHeight: 360 }} /> : <p>이미지 로딩 중…</p>;
}

export function CommunitySubmissions() {
  const { user, status } = useMemberAuth();
  if (!user) return <p>{status === "loading" ? "로그인 확인 중…" : "로그인 후 검사 상태를 확인할 수 있어요."} <Link href="/login">로그인</Link></p>;
  return <SubmissionContent key={user.id} />;
}

function OwnComment({ comment, refresh }: { comment: CommunityComment; refresh: () => void }) {
  const [content, setContent] = useState(comment.content);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function mutate(remove: boolean) {
    if (remove && !window.confirm("댓글을 삭제할까요?")) return;
    setBusy(true); setError("");
    try {
      if (remove) await deleteCommunityComment(comment.id);
      else await updateCommunityComment(comment.id, content);
      refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "요청 실패"); }
    finally { setBusy(false); }
  }
  return <article><p>댓글 #{comment.id} · {moderationLabel[comment.moderationStatus]}</p><textarea aria-label={`댓글 ${comment.id} 수정`} value={content} maxLength={2000} disabled={busy} onChange={event => setContent(event.target.value)} /><div><button disabled={busy || !content.trim()} onClick={() => void mutate(false)}>수정 후 재검사</button> <button disabled={busy} onClick={() => void mutate(true)}>삭제</button></div>{error && <p role="alert">{error}</p>}</article>;
}

function SubmissionContent() {
  const [posts, setPosts] = useState<TeamCommunityPost[]>([]);
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  async function removePost(id: string) {
    if (busy || !window.confirm("게시글을 삭제할까요?")) return;
    setBusy(true); setError("");
    try {
      await deleteCommunityPost(id);
      setTick(value => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "게시글을 삭제하지 못했어요."); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    let active = true;
    void Promise.all([fetchMyCommunityPosts(), fetchMyCommunityComments()]).then(([value, replies]) => { if (active) { setPosts(value); setComments(replies); setError(""); } }).catch(() => { if (active) setError("내 게시글을 불러오지 못했어요."); });
    return () => { active = false; };
  }, [tick]);
  return <section><h1>내 게시 검사 상태</h1><p>새 글과 수정본은 검사 완료까지 비공개입니다. 보류 사유는 운영진이 검토하여 복구할 수 있어요. 별도 운영 제재가 있으면 승인 후에도 비공개입니다.</p><button onClick={() => setTick(value => value + 1)}>상태 새로고침</button>{error && <p role="alert">{error}</p>}{posts.map(post => <article key={post.id} style={{ padding: "1rem 0", borderBottom: "1px solid #ddd" }}><h2>{post.title}</h2><p>{moderationLabel[post.moderationStatus]} · 버전 {post.revision}</p><p style={{ whiteSpace: "pre-wrap" }}>{post.content}</p>{post.images?.map(image => <PrivateCommunityImage key={image.id} id={image.id} />)}<Link href={`/community/write?edit=${encodeURIComponent(post.id)}`}>수정</Link> <button disabled={busy} onClick={() => void removePost(post.id)}>삭제</button>{post.moderationStatus === "approved" && <> · <Link href={post.board === "free" ? `/community?post=${encodeURIComponent(post.id)}` : getTeamBoardHref(post.teamCode, post.id)}>게시판에서 확인</Link></>}</article>)}<h2>내 댓글 검사</h2>{comments.map(comment => <OwnComment key={`${comment.id}:${comment.revision}`} comment={comment} refresh={() => setTick(value => value + 1)} />)}</section>;
}

type Finding = { id: string; code: string; quote: string; reason: string; resolution: string };
type Job = { id: number; postId: string | null; commentId: number | null; revision: number; review_revision: number; state: string; manualHidden: boolean; payload: { text: string; mode: string; is_trade: boolean; is_advertisement: boolean; image_ids: string[] }; findings: Finding[]; evidence?: { political?: { score: number; threshold: number; case_id: string } }; error_code: string };

async function moderationRequest(path: string, body?: unknown) {
  const response = await memberFetch(`/api/v1/community/admin/moderation/${path}`, { method: body === undefined ? "GET" : "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json();
  if (!response.ok) throw new Error(memberError(data, "검토 요청에 실패했어요. 상태를 새로고침해 주세요."));
  return data;
}

function ReviewCard({ job, reload }: { job: Job; reload: () => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function decide(action: "dismiss" | "confirm" | "retry") {
    setBusy(true); setError("");
    try {
      await moderationRequest(`${job.id}/${action === "retry" ? "retry" : "review"}/`, action === "retry" ? {} : { revision: job.revision, review_revision: job.review_revision, finding_ids: selected, action, note });
      reload();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "검토 실패"); }
    finally { setBusy(false); }
  }
  return <article style={{ padding: "1rem", margin: "1rem 0", border: "1px solid #ddd", borderRadius: 8 }}><h3>{job.commentId ? `댓글 ${job.commentId}` : `게시글 ${job.postId}`} · 버전 {job.revision}</h3><p>검사 상태: {job.state} · 검사 모드: {job.payload.mode}{job.payload.is_trade && " · 거래"}{job.payload.is_advertisement && " · 광고"}</p>{job.evidence?.political && <p>정치 유사도 {job.evidence.political.score.toFixed(3)} / 기준 {job.evidence.political.threshold} · 기준 사례 {job.evidence.political.case_id}</p>}{job.manualHidden && <p>별도 운영 제재로 숨겨진 글입니다. 검사 복구로 제재가 해제되지 않습니다.</p>}<p style={{ whiteSpace: "pre-wrap" }}>{job.payload.text}</p>{job.payload.image_ids.map(id => <PrivateCommunityImage key={id} id={id} />)}{job.error_code && <p role="alert">검사 실패: {job.error_code}</p>}{job.findings.map(item => <div key={item.id}><label><input type="checkbox" disabled={busy} checked={selected.includes(item.id)} onChange={event => setSelected(values => event.target.checked ? [...values, item.id] : values.filter(id => id !== item.id))} /> {item.code} · {item.resolution} — {item.reason}</label><blockquote style={{ whiteSpace: "pre-wrap" }}>{item.quote}</blockquote></div>)}{job.state === "done" && job.findings.length > 0 && <><label>검토 근거 <textarea maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label><div><button disabled={busy || !selected.length || !note.trim()} onClick={() => void decide("dismiss")}>선택 사유 해제·복구 검토</button> <button disabled={busy || !selected.length || !note.trim()} onClick={() => void decide("confirm")}>선택 사유 확정·비공개 유지</button></div><p>미해제 사유가 모두 없어야 공개됩니다.</p></>}{job.state === "failed" && <button disabled={busy} onClick={() => void decide("retry")}>검사 재시도</button>}{error && <p role="alert">{error}</p>}</article>;
}

export function ModerationPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [page, setPage] = useState(1);
  const [next, setNext] = useState(false);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void moderationRequest(`?page=${page}`).then(data => { if (active) { setJobs(data.results); setNext(Boolean(data.next)); setError(""); } }).catch(cause => { if (active) { setJobs([]); setError(cause.message); } });
    return () => { active = false; };
  }, [page, tick]);
  return <section><h2>비공개 게시 검사</h2><button onClick={() => setTick(value => value + 1)}>새로고침</button>{error && <p role="alert">{error}</p>}{jobs.map(job => <ReviewCard key={`${job.id}:${job.review_revision}:${tick}`} job={job} reload={() => setTick(value => value + 1)} />)}<button disabled={page === 1} onClick={() => setPage(value => value - 1)}>이전</button> {page} <button disabled={!next} onClick={() => setPage(value => value + 1)}>다음</button></section>;
}
