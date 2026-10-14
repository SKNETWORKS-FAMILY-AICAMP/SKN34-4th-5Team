"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { memberFetch, memberError } from "@/lib/member-auth-request";
import { fetchMyCommunityPosts, fetchMyCommunityComments, deleteCommunityPost, deleteCommunityComment, updateCommunityComment, type CommunityComment } from "@/lib/community-api";
import { useMemberAuth } from "@/lib/member-auth";
import type { TeamCommunityPost } from "@/lib/team-community";
import { getTeamBoardHref } from "@/lib/team-community";
import styles from "./community-moderation.module.css";

export const moderationLabel: Record<string, string> = { pending: "비공개 · 검사 대기", review: "운영진 검토 · 자동 검사 오류 시 공개 가능", error: "비공개 · 검사 오류", rejected: "비공개 · 보류 확정", approved: "검사 승인" };

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
  if (!user) return <section className={styles.submissions}><h1>내 게시 검사 상태</h1><div className={styles.empty}><p role={status === "loading" ? "status" : undefined}>{status === "loading" ? "로그인 확인 중…" : "로그인 후 검사 상태를 확인할 수 있어요."}</p>{status !== "loading" && <Link className="button button-primary" href="/login">로그인</Link>}</div></section>;
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
  return <article className={styles.submissionCard} aria-busy={busy}>
    <header className={styles.submissionHeader}><h3>댓글 #{comment.id}</h3><span className={styles.submissionStatus} data-state={comment.moderationStatus}>{moderationLabel[comment.moderationStatus]}</span></header>
    <p className={styles.metadata}>버전 {comment.revision}</p>
    <textarea className={styles.commentInput} aria-label={`댓글 ${comment.id} 수정`} rows={4} value={content} maxLength={2000} disabled={busy} onChange={event => setContent(event.target.value)} />
    <div className={styles.submissionActions}><button className="button button-primary" disabled={busy || !content.trim()} onClick={() => void mutate(false)}>수정 후 재검사</button><button className={`button button-secondary ${styles.deleteButton}`} disabled={busy} onClick={() => void mutate(true)}>삭제</button></div>
    {busy && <p className={styles.hint} role="status">댓글 요청을 처리하고 있어요.</p>}{error && <p className={styles.error} role="alert">{error}</p>}
  </article>;
}

function SubmissionContent() {
  const [posts, setPosts] = useState<TeamCommunityPost[]>([]);
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
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
    setLoading(true);
    void Promise.all([fetchMyCommunityPosts(), fetchMyCommunityComments()]).then(([value, replies]) => { if (active) { setPosts(value); setComments(replies); setError(""); } }).catch(() => { if (active) setError("내 게시글과 댓글을 불러오지 못했어요."); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tick]);
  return <section className={styles.submissions} aria-labelledby="submissions-heading" aria-busy={loading || busy}>
    <header className={styles.panelHeader}><div><p className="eyebrow">COMMUNITY</p><h1 id="submissions-heading">내 게시 검사 상태</h1><p>새 글과 수정본은 검사 대기 중 비공개입니다. 자동 검사 오류 시 공개와 운영진 검토를 병행하며, 유해성 판정·이미지 검토·수동 모드는 비공개 검토합니다. 별도 운영 제재나 비공개 부모 글은 공개되지 않습니다.</p></div><button className="button button-secondary" disabled={loading || busy} onClick={() => setTick(value => value + 1)}>상태 새로고침</button></header>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {loading && (posts.length > 0 || comments.length > 0) && <p className={styles.hint} role="status">검사 상태를 새로고침하고 있어요.</p>}
    {loading && posts.length === 0 && comments.length === 0 ? <div className={styles.empty} role="status">내 게시글과 댓글을 불러오고 있어요.</div> : <>
      <section aria-labelledby="own-posts-heading"><h2 id="own-posts-heading" className={styles.submissionListHeading}>내 게시글 검사</h2>
        {!loading && !error && posts.length === 0 && <div className={styles.empty}><h3>등록한 게시글이 없어요</h3><p>새 글을 작성하면 이곳에서 검사 상태를 확인할 수 있어요.</p><Link className="button button-primary" href="/community/write">글쓰기</Link></div>}
        {posts.map(post => <article className={styles.submissionCard} key={post.id}>
          <header className={styles.submissionHeader}><h3>{post.title}</h3><span className={styles.submissionStatus} data-state={post.moderationStatus}>{moderationLabel[post.moderationStatus]}</span></header>
          <p className={styles.metadata}>버전 {post.revision}</p><p className={styles.submissionText}>{post.content}</p>
          {post.images && post.images.length > 0 && <div className={styles.attachments}>{post.images.map(image => <PrivateCommunityImage key={image.id} id={image.id} />)}</div>}
          <div className={styles.submissionActions}><Link className="button button-primary" href={`/community/write?edit=${encodeURIComponent(post.id)}`}>수정</Link><button className={`button button-secondary ${styles.deleteButton}`} disabled={busy} onClick={() => void removePost(post.id)}>삭제</button>{post.moderationStatus === "approved" && <Link className="button button-secondary" href={post.board === "free" ? `/community?post=${encodeURIComponent(post.id)}` : getTeamBoardHref(post.teamCode, post.id)}>게시판에서 확인</Link>}</div>
        </article>)}
      </section>
      <section aria-labelledby="own-comments-heading"><h2 id="own-comments-heading" className={styles.submissionListHeading}>내 댓글 검사</h2>
        {!loading && !error && comments.length === 0 && <div className={styles.empty}><h3>등록한 댓글이 없어요</h3><p>게시판에 댓글을 남기면 이곳에서 검사 상태를 확인할 수 있어요.</p><Link className="button button-secondary" href="/community">게시판으로</Link></div>}
        {comments.map(comment => <OwnComment key={`${comment.id}:${comment.revision}`} comment={comment} refresh={() => setTick(value => value + 1)} />)}
      </section>
    </>}
  </section>;
}

type Finding = { id: string; code: string; quote: string; reason: string; resolution: string };
type Job = { id: number; postId: string | null; commentId: number | null; revision: number; review_revision: number; state: string; manualHidden: boolean; payload: { text: string; mode: string; is_trade: boolean; is_advertisement: boolean; image_ids: string[] }; findings: Finding[]; evidence?: { political?: { score: number; threshold: number; case_id: string } }; error_code: string };

const jobLabels: Record<string, string> = { queued: "검사 대기", running: "검사 중", done: "검사 완료", failed: "검사 실패", cancelled: "검사 취소" };
const resolutionLabels: Record<string, string> = { open: "검토 필요", dismissed: "사유 해제", confirmed: "보류 확정" };
const findingLabels: Record<string, string> = { manual_review: "수동 검토", political: "정치 관련" };

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
  return <article className={styles.review} aria-labelledby={`job-${job.id}`} aria-busy={busy}>
    <header className={styles.reviewHeader}>
      <div><h3 id={`job-${job.id}`}>{job.commentId ? "댓글" : "게시글"} 검사 <span>#{job.id}</span></h3>
        <p className={styles.metadata}>버전 {job.revision} · {job.payload.mode === "manual" ? "수동 검토 모드" : job.payload.mode === "openai" ? "자동 검사 모드" : job.payload.mode}{job.payload.is_trade && " · 거래"}{job.payload.is_advertisement && " · 광고"}</p>
      </div>
      <span className={styles.status} data-state={job.state}>{jobLabels[job.state] ?? job.state}</span>
    </header>
    {job.manualHidden && <p className={styles.warning}>별도 운영 제재로 숨겨진 글입니다. 검사 복구로 제재가 해제되지 않습니다.</p>}
    {job.error_code && <p className={styles.error} role="alert">검사 실패: {job.error_code}</p>}
    <div className={styles.reviewBody}>
      <section className={styles.original} aria-label="검사 대상 원문">
        <h4>검사 대상 원문</h4>
        <p className={styles.identifier}>{job.commentId ? `댓글 ID ${job.commentId}` : `게시글 ID ${job.postId}`}</p>
        <p className={styles.originalText}>{job.payload.text}</p>
        {job.payload.image_ids.length > 0 && <div className={styles.attachments}>{job.payload.image_ids.map(id => <PrivateCommunityImage key={id} id={id} />)}</div>}
        {job.evidence?.political && <dl className={styles.evidence}>
          <div><dt>정치 유사도</dt><dd>{job.evidence.political.score.toFixed(3)} <span>/ 기준 {job.evidence.political.threshold}</span></dd></div>
          <div><dt>기준 사례</dt><dd>{job.evidence.political.case_id}</dd></div>
        </dl>}
      </section>
      <section className={styles.decision} aria-label="검사 사유 검토">
        <fieldset className={styles.findings} disabled={busy || job.state !== "done"}>
          <legend>검사 사유 <span>{job.findings.length}건</span></legend>
          {job.findings.length > 0 ? <p className={styles.hint}>처리할 사유를 선택한 뒤 검토 근거를 작성하세요.</p> : <p className={styles.hint}>{job.state === "done" ? "검출된 사유가 없습니다." : "검사 완료 후 판정 사유를 확인할 수 있습니다."}</p>}
          {job.findings.map(item => <label key={item.id} className={styles.finding}>
            <input type="checkbox" disabled={busy} checked={selected.includes(item.id)} onChange={event => setSelected(values => event.target.checked ? [...values, item.id] : values.filter(id => id !== item.id))} />
            <span className={styles.findingContent}>
              <span className={styles.findingTitle}><strong>{findingLabels[item.code] ?? item.code}</strong><span className={styles.resolution} data-resolution={item.resolution}>{resolutionLabels[item.resolution] ?? item.resolution}</span></span>
              <span className={styles.reason}>{item.reason}</span>
              {item.quote && <span className={styles.quote}>“{item.quote}”</span>}
            </span>
          </label>)}
        </fieldset>
        {job.state === "done" && job.findings.length > 0 && <div className={styles.reviewForm}>
          <label htmlFor={`review-note-${job.id}`}>검토 근거 <span>필수</span></label>
          <textarea id={`review-note-${job.id}`} aria-describedby={`review-help-${job.id}`} maxLength={500} rows={3} disabled={busy} value={note} placeholder="원문을 확인한 뒤 사유를 해제하거나 확정하는 근거를 남겨 주세요." onChange={event => setNote(event.target.value)} />
          <div className={styles.formMeta}><span>{selected.length}개 사유 선택</span><span>{note.length}/500</span></div>
          <div className={styles.actions}>
            <button className="button button-primary" disabled={busy || !selected.length || !note.trim()} onClick={() => void decide("dismiss")}>선택 사유 해제·복구 검토</button>
            <button className="button button-secondary" disabled={busy || !selected.length || !note.trim()} onClick={() => void decide("confirm")}>선택 사유 확정·비공개 처리</button>
          </div>
          <p id={`review-help-${job.id}`} className={styles.hint}>자동 검사 오류만 남으면 공개와 사후 검토를 병행합니다. 다른 미해제 사유나 확정 사유가 있으면 비공개입니다. 숨김·비공개 부모 글은 유지됩니다.</p>
          {busy && <p role="status" className={styles.hint}>검토 결과를 저장하고 있어요.</p>}
        </div>}
        {job.state === "failed" && <button className="button button-secondary" disabled={busy} onClick={() => void decide("retry")}>검사 재시도</button>}
        {error && <p className={styles.error} role="alert">{error}</p>}
      </section>
    </div>
  </article>;
}

export function ModerationModeControl() {
  const [mode, setMode] = useState("manual");
  const [saved, setSaved] = useState("");
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function load() {
    setBusy(true); setError("");
    try {
      const data = await moderationRequest("settings/");
      setMode(data.mode); setSaved(data.mode); setAvailable(data.automaticAvailable);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "설정을 불러오지 못했어요."); }
    finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  async function save() {
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await moderationRequest("settings/", { mode });
      setMode(data.mode); setSaved(data.mode); setAvailable(data.automaticAvailable);
      setMessage("저장했습니다. 새 게시글·댓글과 수정본부터 적용됩니다.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "설정을 저장하지 못했어요."); }
    finally { setBusy(false); }
  }
  return <section className={styles.modeSettings} aria-label="검사 방식 설정">
    <h3>검사 방식</h3>
    <p>현재 방식: {saved ? saved === "openai" ? "자동 · OpenAI 기본 검사" : "수동 · 운영진 검토" : "확인 중"}</p>
    <label htmlFor="moderation-mode">새 게시글·댓글 검사 방식 </label>
    <select id="moderation-mode" value={mode} disabled={busy || !saved} onChange={event => { setMode(event.target.value); setMessage(""); }}>
      <option value="manual">수동 · 모든 글 운영진 검토</option>
      <option value="openai" disabled={!available}>자동 · OpenAI 기본 검사</option>
    </select>{" "}
    <button className="button button-primary" disabled={busy || !saved || mode === saved} onClick={() => void save()}>방식 저장</button>{" "}
    <button className="button button-secondary" disabled={busy} onClick={() => void load()}>설정 새로고침</button>
    <p>자동은 omni-moderation-latest의 유해성 판정만 사용합니다. 정치 유사도·별도 문맥 모델은 사용하지 않습니다. 첨부 이미지는 운영진이 확인합니다.</p>
    <p>방식 변경은 기존 검사 내역에는 적용되지 않습니다. 자동 검사 오류는 공개와 운영진 검토를 병행합니다. 이미지 검토·숨김 글·비공개 부모 글의 댓글은 비공개로 유지됩니다.</p>
    {!busy && saved && !available && <p>자동 검사를 사용하려면 서버에 OpenAI API 키를 설정해 주세요.</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}

export function ModerationPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [page, setPage] = useState(1);
  const [next, setNext] = useState(false);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true);
    void moderationRequest(`?page=${page}`).then(data => { if (active) { setJobs(data.results); setNext(Boolean(data.next)); setError(""); } }).catch(cause => { if (active) { setJobs([]); setNext(false); setError(cause.message); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, tick]);
  return <section className={styles.panel} aria-labelledby="moderation-heading">
    <header className={styles.panelHeader}><div><h2 id="moderation-heading">게시 검사·사후 검토</h2><p>게시글과 댓글의 원문을 확인하고, 검사 사유별로 복구 여부를 검토하세요.</p></div><button className="button button-secondary" disabled={loading} onClick={() => setTick(value => value + 1)}>새로고침</button></header>
    <ModerationModeControl />
    <div className={styles.listHeading}><h3>검사 목록</h3><span>{loading ? "불러오는 중" : `현재 페이지 ${jobs.length}건`}</span></div>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {loading ? <div className={styles.empty} role="status">검사 목록을 불러오고 있어요.</div> : <>
      {!error && jobs.length === 0 && <div className={styles.empty}><h3>표시할 검사 내역이 없습니다</h3><p>새 게시글·댓글의 검사 내역은 새로고침하여 확인할 수 있어요.</p></div>}
      {jobs.map(job => <ReviewCard key={`${job.id}:${job.review_revision}:${tick}`} job={job} reload={() => setTick(value => value + 1)} />)}
    </>}
    <nav className={styles.pagination} aria-label="검사 목록 페이지"><button className="button button-secondary" disabled={loading || page === 1} onClick={() => setPage(value => value - 1)}>이전</button><span aria-current="page">{page} 페이지</span><button className="button button-secondary" disabled={loading || !next} onClick={() => setPage(value => value + 1)}>다음</button></nav>
  </section>;
}
