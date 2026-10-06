"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { MAX_MESSAGE_LENGTH } from "@/lib/chat/types";
import { useMemberAuth } from "@/lib/member-auth";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import { ChatAnswer } from "./chat-answer";
import { ChatCourseCard } from "./chat-course-card";
import { ChatPending } from "./chat-pending";
import { ChatProgress } from "./chat-progress";
import "@/styles/chat-workspace.css";

const SUGGESTIONS = [
  { icon: "route", label: "나에게 맞는 직관 코스", text: "잠실에서 첫 직관을 해요. 경기 전후 코스를 추천해 주세요.", intent: "route" },
  { icon: "stadium", label: "처음 가는 구장이 궁금해요", text: "처음 구장에 갈 때 어떤 정보를 확인하면 좋을까요?", intent: "stadium" },
  { icon: "book", label: "야구 규칙 쉽게 알아보기", text: "야구를 처음 보는 사람에게 기본 규칙을 쉽게 알려 주세요.", intent: "baseball" },
] as const;

export function ChatWorkspace() {
  const chat = useChat();
  const { status: authStatus } = useMemberAuth();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const minimizeRef = useRef<HTMLButtonElement>(null);
  const composingRef = useRef(false);
  const nearBottomRef = useRef(true);
  const lastConversationRef = useRef(chat.activeConversationId);
  const drawerFocusRef = useRef<"menu" | "composer">("menu");
  const busy = Boolean(chat.pending);
  const available = authStatus === "authenticated" && Boolean(chat.status?.ready) && !chat.statusLoading && !chat.statusError;
  const empty = chat.messages.length === 0 && !chat.pending && !chat.failed;
  const demo = chat.status?.provider === "demo";
  const guest = authStatus === "anonymous";
  const activeTitle = chat.conversations.find(conversation => conversation.id === chat.activeConversationId)?.title;

  useEffect(() => {
    const input = inputRef.current;
    if (input?.disabled) minimizeRef.current?.focus({ preventScroll: true });
    else input?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 176)}px`;
  }, [chat.draft]);

  useEffect(() => {
    const changedConversation = lastConversationRef.current !== chat.activeConversationId;
    lastConversationRef.current = chat.activeConversationId;
    if (nearBottomRef.current || busy || changedConversation) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "instant" });
    }
  }, [chat.messages, chat.pending, chat.failed, chat.error, chat.activeConversationId, busy]);

  function closeDrawer(focusComposer = false) {
    drawerFocusRef.current = focusComposer ? "composer" : "menu";
    drawerRef.current?.close();
  }

  function startNewChat() {
    chat.onReset();
    closeDrawer(true);
    nearBottomRef.current = true;
    inputRef.current?.focus();
  }

  function send() {
    if (busy || !available || !chat.draft.trim()) return;
    nearBottomRef.current = true;
    chat.onSend();
  }

  function sidebarContent(mobile = false) {
    return (
      <>
        <div className="workspace-sidebar-heading">
          <Link href="/" className="workspace-brand" aria-label="KBO 홈으로" onClick={() => closeDrawer()}>KBO<span /></Link>
          {mobile && <button type="button" className="workspace-icon-button" aria-label="대화 목록 닫기" onClick={() => closeDrawer()}><Icon name="close" size={21} /></button>}
        </div>
        {!guest && <button type="button" className="workspace-new-chat" onClick={startNewChat} disabled={busy || chat.uncertain}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5H5v14h14v-7M10 14l1-4 8-8 3 3-8 8-4 1ZM17 4l3 3" /></svg>
          새 대화
        </button>}
        <div className="workspace-history">
          <h2>최근 대화</h2>
          {chat.conversations.length ? <nav aria-label="최근 대화">{chat.conversations.map(conversation => (
            <div key={conversation.id} className={`workspace-history-row${conversation.id === chat.activeConversationId ? " is-current" : ""}`}>
              <button type="button" className={`workspace-history-item${conversation.id === chat.activeConversationId ? " is-current" : ""}`} aria-current={conversation.id === chat.activeConversationId ? "true" : undefined} disabled={busy || chat.uncertain} title={conversation.title} onClick={() => { chat.onSelectConversation(conversation.id); closeDrawer(); }}>
                <span>{conversation.title}</span>
              </button>
              {/* 마우스를 올리면 오른쪽에 나타나는 삭제 버튼 */}
              <button type="button" className="workspace-history-delete" aria-label={`${conversation.title} 대화 내역 지우기`} title="대화 내역 지우기" disabled={busy && conversation.id === chat.activeConversationId} onClick={() => { if (window.confirm("해당 대화 내역을 지우시겠습니까?")) chat.onDeleteConversation(conversation.id); }}>
                <Icon name="close" size={14} />
              </button>
            </div>
          ))}</nav> : <p className="workspace-history-empty">함께 나눈 이야기가<br />여기에 모여요.</p>}
        </div>
        <nav className="workspace-quick-links" aria-label="직관 준비">
          <span>직관 준비 이어가기</span>
          <Link href="/routes/new" onClick={() => closeDrawer()}><Icon name="route" size={18} />루트 작성</Link>
          <Link href="/routes" onClick={() => closeDrawer()}><Icon name="map" size={18} />루트 둘러보기</Link>
          <Link href="/stadiums" onClick={() => closeDrawer()}><Icon name="stadium" size={18} />구장 알아보기</Link>
        </nav>
        <p className="workspace-sidebar-note">나만의 야구 하루, KBO ROUTE</p>
      </>
    );
  }

  return (
    <div className="chat-workspace">
      <aside className="workspace-sidebar" aria-label="직관 도우미 메뉴">{sidebarContent()}</aside>
      <dialog ref={drawerRef} className="workspace-drawer" aria-label="직관 도우미 메뉴" onClose={() => { if (drawerFocusRef.current === "composer") inputRef.current?.focus(); else menuRef.current?.focus(); }} onCancel={() => { drawerFocusRef.current = "menu"; }} onClick={event => { if (event.target === event.currentTarget) closeDrawer(); }}>
        <div className="workspace-drawer-content">{sidebarContent(true)}</div>
      </dialog>
      <main className="workspace-main">
        <header className="workspace-topbar">
          <button ref={menuRef} type="button" className="workspace-icon-button workspace-menu-button" aria-label="대화 목록 열기" aria-haspopup="dialog" onClick={() => drawerRef.current?.showModal()}><Icon name="menu" size={22} /></button>
          <div className="workspace-title"><h1>직관 도우미</h1><span className={`workspace-connection${available ? " is-ready" : ""}`} aria-live="polite"><i />{guest ? "로그인 후 이용" : chat.statusLoading ? "연결 확인 중" : demo ? "예시 대화" : available ? "함께 준비해요" : "연결 확인 필요"}</span></div>
          {activeTitle && <p className="workspace-active-title" title={activeTitle}>{activeTitle}</p>}
          <div className="workspace-window-actions">
            <button ref={minimizeRef} type="button" className="workspace-icon-button workspace-minimize" aria-label="채팅 작게 보기" title="채팅 작게 보기" onClick={chat.onMinimize}>
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 4l5 5M9 4v5H4M20 20l-5-5M15 20v-5h5" /></svg>
            </button>
            <Link href="/" className="workspace-home-link"><Icon name="home" size={17} /><span>홈으로</span></Link>
          </div>
        </header>

        <div className={`workspace-transcript${empty ? " is-empty" : ""}`} ref={scrollRef} onScroll={event => { const element = event.currentTarget; nearBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; }}>
          <div className="workspace-reading-column">
            {empty && <section className="workspace-welcome" aria-labelledby="workspace-welcome-title">
              <span className="workspace-welcome-mark"><Icon name="sparkles" size={29} /></span>
              <p className="workspace-welcome-eyebrow">야구가 있는 하루를, 함께</p>
              <h2 id="workspace-welcome-title">어떤 직관을 준비하고 있나요?</h2>
              <p className="workspace-welcome-description">{guest ? "비로그인 상태에서는 사이트 내용을 둘러볼 수 있어요. 질문은 로그인 후 보내 주세요." : <>직관 코스부터 구장 정보, 쉬운 야구 이야기까지.<br />궁금한 것을 편하게 물어보세요.</>}</p>
              {!guest && <div className="workspace-suggestions">{SUGGESTIONS.map(item => <button key={item.intent} type="button" onClick={() => { chat.onSuggestion(item.text, item.intent); inputRef.current?.focus(); }}><Icon name={item.icon} size={19} /><span>{item.label}</span></button>)}</div>}
            </section>}
            {chat.context?.stadium && <p className="workspace-context"><Icon name="pin" size={14} />{chat.context.stadium}에서의 하루</p>}
            <div className="workspace-messages" role="log" aria-label="직관 도우미 대화 내용" aria-live="polite" aria-relevant="additions">
              {chat.messages.map((message, index) => <article key={`${chat.activeConversationId}-${index}`} className={`workspace-message workspace-message-${message.role}`}>
                {message.role === "assistant" ? <><div className="workspace-assistant-label"><span><Icon name="sparkles" size={15} /></span>직관 도우미</div><ChatProgress operations={message.progress ?? []} />{message.content && <ChatAnswer text={message.content} />}{message.course && <ChatCourseCard course={message.course} />}</> : <><span className="sr-only">나</span><div className="workspace-user-bubble">{message.content}</div></>}
              </article>)}
              {(chat.pending || chat.failed) && <article className="workspace-message workspace-message-user"><span className="sr-only">나</span><div className="workspace-user-bubble">{chat.pending || chat.failed}</div></article>}
              {(busy || chat.progress.length > 0) && <article className="workspace-message workspace-message-assistant"><div className="workspace-assistant-label"><span><Icon name="sparkles" size={15} /></span>직관 도우미</div><ChatProgress key={chat.progress[0]?.operationId ?? "pending"} operations={chat.progress} />{chat.streaming ? <ChatAnswer text={chat.streaming} /> : <ChatPending busy={busy} streaming={chat.streaming} className="workspace-thinking" />}</article>}
            </div>
            {chat.error && <div className="workspace-feedback is-error" role="alert"><p>{chat.error}</p><button type="button" onClick={chat.uncertain ? chat.onReset : chat.onRetry} disabled={busy || (!chat.uncertain && !available)}>{chat.uncertain ? "새 대화에서 다시 보내기" : "다시 시도"}</button></div>}
            {chat.statusError && <div className="workspace-feedback is-error" role="alert"><p>{chat.statusError}</p><button type="button" onClick={chat.onRefreshStatus} disabled={chat.statusLoading}>연결 다시 확인</button></div>}
            {!chat.statusError && chat.status && !chat.status.ready && !chat.statusLoading && <div className="workspace-feedback"><p>대화 연결을 준비하고 있어요.</p><button type="button" onClick={chat.onRefreshStatus}>연결 다시 확인</button></div>}
            {chat.notice && <p className="workspace-notice" role="status">{chat.notice}</p>}
          </div>
        </div>

        {guest ? <div className="workspace-composer-area workspace-guest-cta"><Link href="/login">로그인하고 질문하기</Link><p>비로그인 상태에서는 조회만 할 수 있어요.</p></div> : <div className="workspace-composer-area">
          <form className="workspace-composer" onSubmit={event => { event.preventDefault(); send(); }}>
            <label className="sr-only" htmlFor="workspace-question">직관 도우미에게 질문</label>
            <textarea ref={inputRef} id="workspace-question" value={chat.draft} maxLength={MAX_MESSAGE_LENGTH} rows={1} placeholder="직관 도우미에게 물어보세요" disabled={busy} onChange={event => chat.onDraftChange(event.target.value)} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={event => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !composingRef.current && event.keyCode !== 229) { event.preventDefault(); send(); }
            }} />
            <div className="workspace-composer-bottom"><span>{busy ? "답변을 준비하고 있어요" : "야구가 궁금한 모든 순간"}</span><div className="workspace-send-group">{chat.draft.length > MAX_MESSAGE_LENGTH * .8 && <span className="workspace-character-count">{chat.draft.length}/{MAX_MESSAGE_LENGTH}</span>}{busy ? <button type="button" className="workspace-send workspace-stop" aria-label="답변 생성 중단" title="받은 답변까지 보관하고 중단" onClick={chat.onCancel}><span /></button> : <button type="submit" className="workspace-send" aria-label="질문 보내기" title="질문 보내기" disabled={!chat.draft.trim() || !available || chat.uncertain}><Icon name="arrow" size={20} /></button>}</div></div>
          </form>
          <p className="workspace-footnote">{demo ? "예시 답변이에요. 실제 일정과 장소 검색 결과는 포함되지 않아요." : "경기 일정과 구장 운영 정보는 방문 전 공식 안내를 확인해 주세요."}</p>
        </div>}
      </main>
    </div>
  );
}
