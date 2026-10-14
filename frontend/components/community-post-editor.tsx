"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { createCommunityPost, getOwnCommunityPost, updateCommunityPost, type CommunityPostInput } from "@/lib/community-api";
import { writablePostCategories } from "@/lib/community-post-category";
import { createCommunitySubmissionKey } from "@/lib/community-post-key";
import { plainRichDoc } from "@/lib/community-rich-content";
import { useMemberAuth } from "@/lib/member-auth";
import { getTeamBoard, getTeamBoardHref, teamBoards, type TeamCommunityPost } from "@/lib/team-community";
import { CommunityRichEditor } from "./community-rich-editor";
import { MemberName } from "./member-name";
import boardStyles from "./community-board.module.css";
import styles from "./community-post-editor.module.css";

type Board = CommunityPostInput["board"];
type Props = { board: Board; teamCode?: string; editId?: string };

function postHref(post: TeamCommunityPost) {
  return post.board === "free" ? `/community?post=${encodeURIComponent(post.id)}` : getTeamBoardHref(post.teamCode, post.id);
}

export function CommunityPostEditor({ board, teamCode = "", editId = "" }: Props) {
  const router = useRouter();
  const { status, user, reload } = useMemberAuth();
  const defaultTeam = getTeamBoard(teamCode)?.code ?? teamBoards[0].code;
  const [draft, setDraft] = useState<CommunityPostInput>({
    board, teamCode: board === "free" ? "" : defaultTeam,
    category: "잡담", title: "", content: "",
  });
  const [editing, setEditing] = useState<TeamCommunityPost | null>(null);
  const [loadingEdit, setLoadingEdit] = useState(Boolean(editId));
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(false);
  const idempotency = useRef({ signature: "", key: "" });

  useEffect(() => {
    if (!editId) return;
    let current = true;
    void getOwnCommunityPost(editId).then(post => {
      if (!current) return;
      setEditing(post);
      setDraft({ board: post.board, teamCode: post.teamCode, category: post.category, title: post.title, content: post.content, contentDoc: post.contentDoc, isTrade: post.isTrade, isAdvertisement: post.isAdvertisement });
      setLoadingEdit(false);
    }).catch(cause => {
      if (!current) return;
      setError(cause instanceof Error ? cause.message : "게시글을 불러오지 못했어요.");
      setLoadingEdit(false);
    });
    return () => { current = false; };
  }, [editId]);

  const categories = writablePostCategories(draft.board);
  const writable = status === "authenticated" && (!editId || editing?.authorId === user?.id);
  const listHref = draft.board === "free" ? "/community" : getTeamBoardHref(draft.teamCode);
  const cancelHref = editing ? postHref(editing) : listHref;
  const initialDoc = useMemo(() => editing ? editing.contentDoc ?? plainRichDoc(editing.content) : null, [editing]);

  function changeBoard(next: Board) {
    setDraft(value => ({ ...value, board: next, teamCode: next === "free" ? "" : getTeamBoard(value.teamCode)?.code ?? getTeamBoard(user?.team_code ?? "")?.code ?? teamBoards[0].code,
      category: writablePostCategories(next).includes(value.category) ? value.category : "잡담" }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current || !writable) return;
    const input: CommunityPostInput = { ...draft, teamCode: draft.board === "free" ? "" : draft.teamCode, title: draft.title.trim(), content: draft.content.trim() };
    if (uploading || !input.contentDoc || input.content.length > 20000) return;
    const signature = JSON.stringify(input);
    if (idempotency.current.signature !== signature) idempotency.current = { signature, key: createCommunitySubmissionKey() };
    request.current = true; setSaving(true); setError("");
    try {
      const saved = editing ? await updateCommunityPost(editing.id, input) : await createCommunityPost(input, idempotency.current.key);
      router.replace(postHref(saved));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "게시글을 저장하지 못했어요.");
    } finally {
      request.current = false; setSaving(false);
    }
  }

  if (status !== "authenticated") return <main className={`container ${boardStyles.page} ${styles.page} header-aligned-content`}>
    <p className="eyebrow">COMMUNITY</p><h1>{editId ? "게시글 수정" : "게시글 작성"}</h1>
    {status === "loading" ? <p role="status" className={styles.notice}>로그인 상태를 확인하고 있어요.</p>
      : status === "anonymous" ? <div role="status" className={styles.notice}><p>비로그인 상태에서는 게시글을 둘러볼 수 있어요. 글쓰기는 로그인 후 이용해 주세요.</p><Link href="/login">로그인하기</Link> · <Link href="/community">게시판 둘러보기</Link></div>
        : <div role="alert" className={styles.notice}><p>로그인 상태를 확인하지 못했어요.</p><button type="button" onClick={() => void reload()}>다시 확인</button></div>}
  </main>;

  return <main className={`container ${boardStyles.page} ${styles.page} header-aligned-content`}>
    <p className="eyebrow">COMMUNITY</p>
    <h1>{editId ? "게시글 수정" : "게시글 작성"}</h1>
    <p className={boardStyles.description}>저장 후 비공개로 검사합니다. 승인되면 선택한 게시판에 공개됩니다.</p>
    {loadingEdit ? <p role="status" className={styles.notice}>게시글을 불러오고 있어요.</p> : editId && !editing ? <div className={styles.notice} role="alert">{error || "게시글을 찾지 못했어요."} <Link href="/community">게시판으로 돌아가기</Link></div> : <form onSubmit={submit} className={`${boardStyles.postDetail} ${styles.form}`}>
      <div className={boardStyles.postHeader}>
        <div className={styles.options}>
          <label>게시판<select value={draft.board} onChange={event => changeBoard(event.target.value as Board)} disabled={saving}><option value="free">자유 게시판</option><option value="teams">팀 게시판</option></select></label>
          {draft.board === "teams" && <label>팀<select value={draft.teamCode} onChange={event => setDraft(value => ({ ...value, teamCode: event.target.value }))} disabled={saving}>{teamBoards.map(team => <option key={team.code} value={team.code}>{team.shortName} 팀 게시판</option>)}</select></label>}
        </div>
        <fieldset className={styles.categories} disabled={saving}>
          <legend>분류</legend>
          <div>{categories.map(category => <label key={category} className={styles.category}><input type="radio" name="category" value={category} checked={draft.category === category} onChange={() => setDraft(value => ({ ...value, category }))} /><span>{category}</span></label>)}</div>
        </fieldset>
        <label className={styles.titleLabel} htmlFor="community-post-title">제목 <span>{draft.title.length} / 200자</span></label>
        <input id="community-post-title" className={styles.titleInput} value={draft.title} onChange={event => setDraft(value => ({ ...value, title: event.target.value }))} maxLength={200} placeholder="제목을 입력해 주세요." required disabled={saving} />
        <p className={styles.byline}>작성자 <strong><MemberName name={editing?.author ?? user?.nickname ?? user?.username ?? "로그인 필요"} rank={editing?.authorRank ?? user?.rank} size="community" /></strong></p>
      </div>
      <div className={`${boardStyles.postBody} ${styles.body}`}>
        <label htmlFor="community-post-content">내용</label>
        <CommunityRichEditor initial={initialDoc} disabled={saving || !writable} onChange={(contentDoc, content) => setDraft(value => ({ ...value, contentDoc, content }))} onUploadingChange={setUploading} onError={setError} />
      </div>
      <div className={styles.footer}>
        {editId && editing && editing.authorId !== user?.id && <p role="alert">작성자만 이 글을 수정할 수 있어요.</p>}
        {error && <p role="alert">{error}</p>}
        <div className={styles.actions}><Link href={cancelHref}>취소</Link><button type="submit" disabled={!writable || saving || uploading || !draft.title.trim() || !draft.content.trim() || !draft.contentDoc || draft.content.length > 20000}>{saving ? "등록 중…" : editId ? "수정 완료" : "등록"}</button></div>
      </div>
    </form>}
  </main>;
}
