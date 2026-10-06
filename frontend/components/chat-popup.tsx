"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef } from "react";
import { MAX_MESSAGE_LENGTH } from "@/lib/chat/types";
import { useMemberAuth } from "@/lib/member-auth";
import { ChatAnswer } from "./chat-answer";
import { ChatCourseCard } from "./chat-course-card";
import { ChatPending } from "./chat-pending";
import { ChatProgress } from "./chat-progress";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import "@/styles/chat-popup.css";

const SUGGESTIONS = [
  { icon: "route", label: "직관 코스 추천", text: "잠실에서 첫 직관을 해요. 경기 전후 코스를 추천해 주세요.", intent: "route" },
  { icon: "stadium", label: "구장 정보", text: "처음 구장에 갈 때 어떤 정보를 확인하면 좋을까요?", intent: "stadium" },
  { icon: "book", label: "쉬운 야구 규칙", text: "야구를 처음 보는 사람에게 기본 규칙을 쉽게 알려 주세요.", intent: "baseball" },
] as const;

type ChatPopupProps = {
  embedded?: boolean;
  title?: string;
  conversationLabel?: string | null;
  welcomeTitle?: string;
  welcomeDescription?: ReactNode | null;
  welcomeLink?: { href: string; label: string };
};

export function ChatPopup({
  embedded = false,
  title = "직관 도우미",
  conversationLabel = "나의 직관 이야기",
  welcomeTitle = "어떤 직관을 준비하고 있나요?",
  welcomeDescription = <>직관 코스부터 구장 정보, 야구 이야기까지.<br />궁금한 것을 편하게 물어보세요.</>,
  welcomeLink,
}: ChatPopupProps) {
  const chat = useChat();
  const { status: authStatus } = useMemberAuth();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const nearBottomRef = useRef(true);
  const lastConversationRef = useRef(chat.activeConversationId);
  const busy = Boolean(chat.pending);
  const available = authStatus === "authenticated" && Boolean(chat.status?.ready) && !chat.statusLoading && !chat.statusError;
  const empty = chat.messages.length === 0 && !chat.pending && !chat.failed;
  const demo = chat.status?.provider === "demo";
  const guest = authStatus === "anonymous";
  const titleId = embedded ? "writer-chat-title" : "chat-popup-title";
  const conversationId = embedded ? "writer-chat-conversation" : "chat-popup-conversation";
  const questionId = embedded ? "writer-chat-question" : "chat-popup-question";

  useEffect(() => {
    if (embedded) return;
    const input = inputRef.current;
    if (input && !input.disabled) input.focus({ preventScroll: true });
    else closeRef.current?.focus({ preventScroll: true });
  }, [embedded]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 104)}px`;
  }, [chat.draft]);

  useEffect(() => {
    const changedConversation = lastConversationRef.current !== chat.activeConversationId;
    lastConversationRef.current = chat.activeConversationId;
    if (nearBottomRef.current || busy || changedConversation) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "instant" });
    }
  }, [chat.messages, chat.pending, chat.failed, chat.error, chat.activeConversationId, busy]);

  function send() {
    if (busy || !available || !chat.draft.trim()) return;
    nearBottomRef.current = true;
    chat.onSend();
  }

  return (
    <section className={`chat-popup${embedded ? " chat-popup-embedded" : ""}`} role={embedded ? "region" : "dialog"} aria-modal={embedded ? undefined : false} aria-labelledby={titleId} onKeyDown={event => {
      if (!embedded && event.key === "Escape" && !event.nativeEvent.isComposing && !composingRef.current) {
        event.preventDefault();
        event.stopPropagation();
        chat.onClosePopup();
      }
    }}>
      <header className="chat-popup-header">
        <span className="chat-popup-mark"><Icon name="sparkles" size={21} /></span>
        <div className="chat-popup-heading"><h2 id={titleId}>{title}</h2><span className={`chat-popup-connection${available ? " is-ready" : ""}`} aria-live="polite"><i />{guest ? "로그인 후 이용" : chat.statusLoading ? "연결 확인 중" : demo ? "예시 대화" : available ? "직관 도우미 연결됨" : "연결 확인 필요"}</span></div>
        <div className="chat-popup-header-actions">
          <button type="button" className="chat-popup-icon-button" aria-label="채팅 크게 보기" title="채팅 크게 보기" onClick={chat.onExpand}><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7" /></svg></button>
          {!embedded && <button ref={closeRef} type="button" className="chat-popup-icon-button" aria-label="직관 도우미 닫기" title="직관 도우미 닫기" onClick={chat.onClosePopup}><Icon name="close" size={20} /></button>}
        </div>
      </header>

      <div className="chat-popup-toolbar">
        {chat.conversations.length > 1 ? <><label className="sr-only" htmlFor={conversationId}>대화 선택</label><select id={conversationId} value={chat.activeConversationId} disabled={busy || chat.uncertain} onChange={event => chat.onSelectConversation(event.target.value)}>{chat.conversations.map(conversation => <option key={conversation.id} value={conversation.id}>{conversation.title}</option>)}</select></> : conversationLabel ? <span>{conversationLabel}</span> : null}
        {!guest && <button type="button" className="chat-popup-new" disabled={busy || chat.uncertain} onClick={() => { chat.onReset(); nearBottomRef.current = true; inputRef.current?.focus(); }}><span aria-hidden="true">+</span>새 대화</button>}
      </div>

      <div ref={scrollRef} className={`chat-popup-transcript${empty ? " is-empty" : ""}`} onScroll={event => { const element = event.currentTarget; nearBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }}>
        {empty && <div className="chat-popup-welcome">
          <span className="chat-popup-welcome-mark"><Icon name="sparkles" size={24} /></span>
          <h3>{welcomeTitle}</h3>
          {guest ? <p>비로그인 상태에서는 사이트 내용을 둘러볼 수 있어요. 질문은 로그인 후 보내 주세요.</p> : welcomeDescription && <p>{welcomeDescription}</p>}
          {!guest && <div className="chat-popup-suggestions">{welcomeLink ? <Link href={welcomeLink.href}><Icon name="route" size={17} />{welcomeLink.label}</Link> : SUGGESTIONS.map(item => <button key={item.intent} type="button" onClick={() => { chat.onSuggestion(item.intent === "route" && chat.context?.stadium ? `${chat.context.stadium}에서 첫 직관을 해요. 경기 전후 코스를 추천해 주세요.` : item.text, item.intent); inputRef.current?.focus(); }}><Icon name={item.icon} size={17} />{item.label}</button>)}</div>}
        </div>}
        {chat.context?.stadium && <p className="chat-popup-context"><Icon name="pin" size={13} />{chat.context.stadium}에서의 하루</p>}
        <div className="chat-popup-messages" role="log" aria-label="직관 도우미 대화 내용" aria-live="polite" aria-relevant="additions">
          {chat.messages.map((message, index) => <article key={`${chat.activeConversationId}-${index}`} className={`chat-popup-message chat-popup-message-${message.role}`}>
            {message.role === "assistant" ? <><div className="chat-popup-assistant-label"><Icon name="sparkles" size={14} />직관 도우미</div><ChatProgress operations={message.progress ?? []} />{message.content && <ChatAnswer text={message.content} />}{message.course && <ChatCourseCard course={message.course} />}</> : <><span className="sr-only">나</span><div className="chat-popup-user-bubble">{message.content}</div></>}
          </article>)}
          {(chat.pending || chat.failed) && <article className="chat-popup-message chat-popup-message-user"><span className="sr-only">나</span><div className="chat-popup-user-bubble">{chat.pending || chat.failed}</div></article>}
          {(busy || chat.progress.length > 0) && <article className="chat-popup-message chat-popup-message-assistant"><div className="chat-popup-assistant-label"><Icon name="sparkles" size={14} />직관 도우미</div><ChatProgress key={chat.progress[0]?.operationId ?? "pending"} operations={chat.progress} />{chat.streaming ? <ChatAnswer text={chat.streaming} /> : <ChatPending busy={busy} streaming={chat.streaming} className="chat-popup-thinking" />}</article>}
        </div>
        {chat.error && <div className="chat-popup-feedback is-error" role="alert"><p>{chat.error}</p><button type="button" onClick={chat.uncertain ? chat.onReset : chat.onRetry} disabled={busy || (!chat.uncertain && !available)}>{chat.uncertain ? "새 대화에서 다시 보내기" : "다시 시도"}</button></div>}
        {chat.statusError && <div className="chat-popup-feedback is-error" role="alert"><p>{chat.statusError}</p><button type="button" onClick={chat.onRefreshStatus} disabled={chat.statusLoading}>연결 다시 확인</button></div>}
        {!chat.statusError && chat.status && !chat.status.ready && !chat.statusLoading && <div className="chat-popup-feedback"><p>대화 연결을 준비하고 있어요.</p><button type="button" onClick={chat.onRefreshStatus}>연결 다시 확인</button></div>}
        {chat.notice && <p className="chat-popup-notice" role="status">{chat.notice}</p>}
      </div>

      {guest ? <div className="chat-popup-composer-area chat-popup-guest-cta"><Link href="/login">로그인하고 질문하기</Link><p>비로그인 상태에서는 조회만 할 수 있어요.</p></div> : <div className="chat-popup-composer-area">
        <div className="chat-popup-composer">
          <label className="sr-only" htmlFor={questionId}>직관 도우미에게 질문</label>
          <textarea ref={inputRef} id={questionId} value={chat.draft} maxLength={MAX_MESSAGE_LENGTH} rows={1} placeholder="직관 도우미에게 물어보세요" disabled={busy} onChange={event => chat.onDraftChange(event.target.value)} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={event => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !composingRef.current && event.keyCode !== 229) { event.preventDefault(); send(); }
          }} />
          <div className="chat-popup-composer-bottom"><span>{chat.draft.length > MAX_MESSAGE_LENGTH * .8 ? `${chat.draft.length}/${MAX_MESSAGE_LENGTH}` : busy ? "답변을 준비하고 있어요" : "야구가 궁금한 모든 순간"}</span>{busy ? <button type="button" className="chat-popup-send chat-popup-stop" aria-label="답변 생성 중단" title="받은 답변까지 보관하고 중단" onClick={chat.onCancel}><span /></button> : <button type="button" className="chat-popup-send" aria-label="질문 보내기" title="질문 보내기" disabled={!chat.draft.trim() || !available || chat.uncertain} onClick={send}><Icon name="arrow" size={19} /></button>}</div>
        </div>
        <p className="chat-popup-footnote">{demo ? "예시 답변이에요. 실제 검색 결과는 포함되지 않아요." : "일정과 구장 운영 정보는 방문 전 공식 안내를 확인해 주세요."}</p>
      </div>}
    </section>
  );
}
