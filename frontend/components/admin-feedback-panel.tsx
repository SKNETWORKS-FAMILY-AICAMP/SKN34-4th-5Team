"use client";

import { useEffect, useState } from "react";
import { useMemberAuth } from "@/lib/member-auth";
import { FEEDBACK_REASONS, fetchAdminFeedback, fetchAdminFeedbackDetail, type AdminAnswerFeedback } from "@/lib/chat/client";
import panelStyles from "./admin-panels.module.css";
import styles from "./admin-feedback-panel.module.css";

export function AdminFeedbackPanel() {
  const { status, user } = useMemberAuth();
  const allowed = status === "authenticated" && user?.is_superuser === true;
  const identity = allowed ? user.id : null;
  const [page, setPage] = useState(1);
  const [rating, setRating] = useState("");
  const [reason, setReason] = useState("");
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<{ count: number; results: AdminAnswerFeedback[] } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<AdminAnswerFeedback | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) { setData(null); setDetail(null); setSelected(null); setError(""); setBusy(true); }
    });
    void fetchAdminFeedback(page, rating, reason, controller.signal).then(value => { if (!controller.signal.aborted) setData(value); }, cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "목록 조회 실패"); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [allowed, identity, page, rating, reason, reload]);
  useEffect(() => {
    if (!allowed || selected === null) return;
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) setDetail(null); });
    void fetchAdminFeedbackDetail(selected, controller.signal).then(value => { if (!controller.signal.aborted) setDetail(value); }, cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "상세 조회 실패"); });
    return () => controller.abort();
  }, [allowed, identity, selected]);
  return <section className={`${panelStyles.panel} ${styles.panel}`} aria-labelledby="admin-feedback-title">
    <h2 id="admin-feedback-title">챗봇 답변 평가</h2>
    {!allowed ? <p role="status">{status === "loading" ? "권한 확인 중…" : "최고 관리자만 평가를 조회할 수 있어요."}</p> : <>
      <p className={panelStyles.intro}>평가 당시 질문·답변을 검토합니다. 자동 학습·LangSmith 연동은 하지 않습니다.</p>
      <div className={styles.filters}>
        <label>평가 <select value={rating} onChange={event => { setRating(event.target.value); setPage(1); }}><option value="">전체</option><option value="up">좋아요</option><option value="down">아쉬워요</option></select></label>
        <label>사유 <select value={reason} onChange={event => { setReason(event.target.value); setPage(1); }}><option value="">전체</option>{Object.entries(FEEDBACK_REASONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>새로고침</button>
      </div>
      {error && <p role="alert">{error}</p>}
      <p role="status">{busy ? "평가 불러오는 중…" : data ? `${data.count}건 · ${page}페이지` : ""}</p>
      {data && <><ul className={styles.list}>{data.results.map(row => <li key={row.id}><button type="button" aria-pressed={selected === row.id} onClick={() => { setError(""); setSelected(row.id); }}>{row.rating === "up" ? "👍 좋아요" : "👎 아쉬워요"} · {row.question.slice(0, 100)}<small>{new Date(row.updated_at).toLocaleString("ko-KR")}</small></button></li>)}</ul>{!data.results.length && <p>해당 평가가 없습니다.</p>}
        <nav className={styles.filters} aria-label="평가 페이지"><button type="button" disabled={busy || page === 1} onClick={() => setPage(value => value - 1)}>이전</button><button type="button" disabled={busy || page * 20 >= data.count} onClick={() => setPage(value => value + 1)}>다음</button></nav></>}
      {selected !== null && !detail && !error && <p role="status">상세 불러오는 중…</p>}
      {detail && selected === detail.id && <section className={styles.detail} aria-label="평가 상세"><h2>평가 #{detail.id}</h2><dl><dt>평가</dt><dd>{detail.rating === "up" ? "좋아요" : "아쉬워요"}</dd><dt>사유</dt><dd>{FEEDBACK_REASONS[detail.reason as keyof typeof FEEDBACK_REASONS] ?? "없음"}</dd><dt>의견</dt><dd>{detail.comment || "없음"}</dd><dt>질문 스냅샷</dt><dd>{detail.question}</dd><dt>답변 스냅샷</dt><dd>{detail.answer}</dd><dt>세션 / 실제 답변 ID / 공개 번호</dt><dd>{detail.session_id} / {detail.answer_id} / {detail.message_id}</dd><dt>실제 저장 메타데이터</dt><dd><pre>{JSON.stringify(detail.metadata, null, 2)}</pre></dd></dl><button type="button" onClick={() => setSelected(null)}>상세 닫기</button></section>}
    </>}
  </section>;
}
