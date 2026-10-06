"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { ChatContext, ChatCourse, ChatMessage, ChatProgressOperation, ChatStatus } from "@/lib/chat/types";
import { MAX_HISTORY_MESSAGES, MAX_MESSAGE_LENGTH } from "@/lib/chat/types";
import {
  ChatClientError,
  deleteChatSession,
  fetchChatHistory,
  fetchChatTurns,
  getChatStatus,
  listChatSessions,
  sendChatMessage,
  sendGuestChatMessage,
  type ChatCheckpoint,
} from "@/lib/chat/client";
import { reduceProgress, settleProgress } from "@/lib/chat/progress";
import { commitChatLoad, restoreChatMessages } from "@/lib/chat/history";
import { useMemberAuth } from "@/lib/member-auth";
import { createClientId } from "@/lib/client-id";
import { ChatPopup } from "./chat-popup";

type ConversationSnapshot = {
  messages: ChatMessage[];
  draft: string;
  context?: ChatContext;
  failed: string;
  failedContext?: ChatContext;
  error: string;
  notice: string;
  uncertain: boolean;
  progress: ChatProgressOperation[];
};
/** 챗봇 코스를 받아 줄 화면 (루트 작성). apply 는 되돌리기 함수를 돌려준다. */
export type CourseTarget = {
  stadiumCode: string;
  stopCount: number;
  apply: (course: ChatCourse, how: "replace" | "append") => (() => void) | null;
};
export type AppliedCourse = { undo: (() => void) | null; message: string };
type ChatControls = ConversationSnapshot & {
  openChat: (initialMessage?: string, context?: ChatContext) => void;
  onExpand: () => void;
  onMinimize: () => void;
  onClosePopup: () => void;
  status: ChatStatus | null;
  statusLoading: boolean;
  statusError: string;
  pending: string;
  streaming: string;
  progress: ChatProgressOperation[];
  uncertain: boolean;
  conversations: { id: string; title: string }[];
  activeConversationId: string;
  onDraftChange: (value: string) => void;
  onRefreshStatus: () => void;
  onSend: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onReset: () => void;
  onSuggestion: (text: string, intent: ChatContext["intent"]) => void;
  onSelectConversation: (id: string) => void;
  /** 왼쪽 대화 목록에서 대화를 지운다 (화면에서 바로 빼고, 서버 기록 삭제는 가능한 경우에만 시도) */
  onDeleteConversation: (id: string) => void;
  onContextChange: (context?: ChatContext) => void;
  courseTarget: CourseTarget | null;
  registerCourseTarget: (target: CourseTarget | null) => void;
  openCourseInWriter: (course: ChatCourse) => void;
  takePendingCourse: () => ChatCourse | null;
  appliedCourses: ReadonlyMap<ChatCourse, AppliedCourse>;
  applyChatCourse: (course: ChatCourse, how: "replace" | "append") => void;
  undoChatCourse: (course: ChatCourse) => void;
};
const ChatControlsContext = createContext<ChatControls | null>(null);

export function useChat() {
  const value = useContext(ChatControlsContext);
  if (!value) throw new Error("useChat must be used inside ChatProvider");
  return value;
}

/**
 * 가이드 샘플 화면용 챗봇: 실제 대화·요청 없이 빈 대화 화면만 보여 준다 (연결 상태 표시는 실제 값을 따른다).
 */
export function ChatSampleProvider({ children }: { children: React.ReactNode }) {
  const real = useChat();
  const noop = () => {};
  const value: ChatControls = {
    ...real,
    messages: [], draft: "", context: undefined,
    failed: "", error: "", notice: "", uncertain: false,
    pending: "", streaming: "", progress: [],
    conversations: [{ id: "guide-sample", title: "새 대화" }], activeConversationId: "guide-sample",
    openChat: noop, onExpand: noop, onMinimize: noop, onClosePopup: noop,
    onDraftChange: noop, onRefreshStatus: noop, onSend: noop, onRetry: noop, onCancel: noop, onReset: noop,
    onSuggestion: noop, onSelectConversation: noop, onDeleteConversation: noop, onContextChange: noop,
    // 샘플 화면은 실제 챗봇 코스를 받지 않는다
    courseTarget: null, registerCourseTarget: noop, openCourseInWriter: noop, takePendingCourse: () => null,
    appliedCourses: new Map(), applyChatCourse: noop, undoChatCourse: noop,
  };
  return <ChatControlsContext.Provider value={value}>{children}</ChatControlsContext.Provider>;
}

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const { status: memberStatus, user } = useMemberAuth();
  const accountId = memberStatus === "authenticated" ? user!.id : null;
  const identity = memberStatus === "authenticated" ? `member:${accountId}` : memberStatus;
  const pathname = usePathname();
  const router = useRouter();
  const isChatPage = pathname === "/chat";
  const hasEmbeddedChat = pathname === "/routes/new";
  const [popupRequested, setPopupRequested] = useState(false);
  const popupOpen = popupRequested && !isChatPage && !hasEmbeddedChat;
  const [activeConversationId, setActiveConversationId] = useState("initial-chat");
  const [conversations, setConversations] = useState([{ id: "initial-chat", title: "새 대화" }]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [context, setContext] = useState<ChatContext | undefined>();
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState("");
  const [pending, setPending] = useState("");
  const [failed, setFailed] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [streaming, setStreaming] = useState("");
  const [progress, setProgress] = useState<ChatProgressOperation[]>([]);
  const [chatIdentity, setChatIdentity] = useState(identity);
  const [courseTarget, setCourseTarget] = useState<CourseTarget | null>(null);
  const courseTargetRef = useRef<CourseTarget | null>(null);
  const [appliedCourses, setAppliedCourses] = useState<ReadonlyMap<ChatCourse, AppliedCourse>>(() => new Map());
  const appliedCoursesRef = useRef(appliedCourses);
  useEffect(() => { appliedCoursesRef.current = appliedCourses; }, [appliedCourses]);
  // 다른 화면(전체 채팅·팝업)에서 "루트 작성에서 열기"를 누르면 여기 두었다가 작성 화면이 가져간다.
  const pendingCourseRef = useRef<ChatCourse | null>(null);
  const streamingRef = useRef("");
  const progressRef = useRef<ChatProgressOperation[]>([]);
  const historyRef = useRef<ChatMessage[]>([]);
  const requestRef = useRef<{
    controller: AbortController;
    identityController: AbortController;
    version: number;
    mode: "member" | "guest";
    checkpoint: ChatCheckpoint | null;
    stop: ChatCheckpoint | null;
    wantsStop: boolean;
  } | null>(null);
  const statusRequestRef = useRef<AbortController | null>(null);
  const historyRequestRef = useRef<AbortController | null>(null);
  const loadingConversationRef = useRef<string | null>(null);
  const requestVersion = useRef(0);
  const pendingRef = useRef("");
  const failedContextRef = useRef<ChatContext | undefined>(undefined);
  const returnPageRef = useRef({ url: "/", scrollY: 0 });
  const restorePageRef = useRef(false);
  const popupOpenerRef = useRef<HTMLElement | null>(null);
  const topButtonRef = useRef<HTMLButtonElement>(null);
  const identityRef = useRef(identity);
  const activeConversationRef = useRef(activeConversationId);
  const identityChanged = chatIdentity !== identity;
  // Root layout keeps conversations alive across client-side page navigation.
  const backendSessions = useRef(new Map<string, number>());
  const archivedConversations = useRef(new Map<string, ConversationSnapshot>());

  const invalidateHistory = useCallback(() => {
    historyRequestRef.current?.abort();
    historyRequestRef.current = null;
    loadingConversationRef.current = null;
  }, []);

  const changeDraft = useCallback((value: string) => {
    if (!loadingConversationRef.current) invalidateHistory();
    setDraft(value);
  }, [invalidateHistory]);

  const loadStatus = useCallback((controller: AbortController) => {
    return getChatStatus(controller.signal).then(
      nextStatus => {
        if (!controller.signal.aborted) { setStatus(nextStatus); setStatusError(""); }
      },
      cause => {
        if (!controller.signal.aborted) {
          setStatus(null);
          setStatusError(cause instanceof Error ? cause.message : "연결 상태를 확인하지 못했어요.");
        }
      },
    ).finally(() => {
      if (!controller.signal.aborted) setStatusLoading(false);
    });
  }, []);

  const refreshStatus = useCallback(() => {
    statusRequestRef.current?.abort();
    if (memberStatus === "anonymous") {
      // 비로그인 상태에서는 챗봇을 둘러보기만 할 수 있다 (질문은 로그인 후)
      setStatus(null);
      setStatusLoading(false);
      setStatusError("");
      return;
    }
    if (memberStatus !== "authenticated") {
      setStatus(null); setStatusLoading(memberStatus === "loading");
      setStatusError(memberStatus === "unavailable" ? "로그인 상태를 확인하지 못했어요." : "");
      return;
    }
    const controller = new AbortController();
    statusRequestRef.current = controller;
    setStatusLoading(true);
    setStatusError("");
    void loadStatus(controller);
  }, [loadStatus, memberStatus]);

  const closePopup = useCallback(() => {
    setPopupRequested(false);
    requestAnimationFrame(() => {
      const opener = popupOpenerRef.current;
      if (opener?.isConnected) opener.focus({ preventScroll: true });
      else topButtonRef.current?.focus({ preventScroll: true });
    });
  }, []);

  const expandChat = useCallback(() => {
    setPopupRequested(false);
    if (!isChatPage) {
      returnPageRef.current = { url: `${window.location.pathname}${window.location.search}${window.location.hash}`, scrollY: window.scrollY };
      router.push("/chat");
    }
  }, [isChatPage, router]);

  const minimizeChat = useCallback(() => {
    // Returning to the embedded assistant must not leave a hidden popup request.
    const destination = isChatPage
      ? new URL(returnPageRef.current.url, window.location.origin).pathname
      : pathname;
    setPopupRequested(destination !== "/routes/new");
    popupOpenerRef.current = null;
    if (isChatPage) {
      restorePageRef.current = true;
      router.push(returnPageRef.current.url, { scroll: false });
    }
  }, [isChatPage, pathname, router]);

  const cancelRequest = useCallback(() => {
    const active = requestRef.current;
    if (!active || active.wantsStop) return;
    active.wantsStop = true;
    if (active.checkpoint) {
      active.stop = active.checkpoint;
      active.controller.abort();
    }
    setNotice("받은 답변까지만 저장하고 있어요.");
    setError("");
  }, []);

  const archiveCurrentConversation = useCallback(() => {
    if (loadingConversationRef.current === activeConversationId) return;
    archivedConversations.current.set(activeConversationId, {
      messages: historyRef.current, draft, context, failed, failedContext: failedContextRef.current, error, notice,
      uncertain, progress: progressRef.current,
    });
  }, [activeConversationId, context, draft, error, failed, notice, uncertain]);

  const resetChat = useCallback(() => {
    if (requestRef.current) return;
    if (!historyRef.current.length && !draft.trim() && !failed && !uncertain) {
      invalidateHistory();
      setContext(undefined);
      setNotice("");
      return;
    }
    archiveCurrentConversation();
    invalidateHistory();
    const carryDraft = uncertain ? draft : "";
    const id = createClientId();
    activeConversationRef.current = id;
    setActiveConversationId(id);
    setConversations(current => [{ id, title: "새 대화" }, ...current]);
    historyRef.current = [];
    failedContextRef.current = undefined;
    setMessages([]);
    setDraft(carryDraft);
    setPending("");
    setStreaming("");
    progressRef.current = [];
    setProgress([]);
    setFailed("");
    setError("");
    setNotice(carryDraft ? "새 대화에서 질문을 확인한 뒤 보내 주세요." : "");
    setUncertain(false);
    setContext(undefined);
  }, [archiveCurrentConversation, draft, failed, invalidateHistory, uncertain]);

  const showConversation = useCallback((saved: ConversationSnapshot, preserveDraft = false) => {
    historyRef.current = saved.messages;
    failedContextRef.current = saved.failedContext;
    progressRef.current = saved.progress;
    setMessages(saved.messages);
    setDraft(current => preserveDraft && current.trim() ? current : saved.draft);
    setContext(saved.context);
    setFailed(saved.failed);
    setError(saved.error);
    setNotice(saved.notice);
    setUncertain(saved.uncertain);
    setProgress(saved.progress);
    setStreaming("");
  }, []);

  const restoreConversation = useCallback(async (id: string, sessionId: number, controller: AbortController, expectedIdentity: string) => {
    try {
      const [history, turns] = await Promise.all([fetchChatHistory(sessionId, controller.signal), fetchChatTurns(sessionId, controller.signal)]);
      const saved: ConversationSnapshot = { messages: restoreChatMessages(history, turns), draft: "", failed: "", error: "", notice: "", uncertain: false, progress: [] };
      commitChatLoad(controller.signal, () => identityRef.current === expectedIdentity && activeConversationRef.current === id, () => {
        loadingConversationRef.current = null;
        archivedConversations.current.set(id, saved);
        showConversation(saved, true);
      });
    } catch (cause) {
      if (!controller.signal.aborted && identityRef.current === expectedIdentity && activeConversationRef.current === id) {
        loadingConversationRef.current = null;
        setNotice("");
        setError(cause instanceof Error ? cause.message : "대화 기록을 불러오지 못했어요.");
      }
    }
  }, [showConversation]);

  const selectConversation = useCallback((id: string) => {
    if (requestRef.current || uncertain || id === activeConversationId) return;
    const saved = archivedConversations.current.get(id);
    archiveCurrentConversation();
    invalidateHistory();
    setActiveConversationId(id);
    activeConversationRef.current = id;
    if (saved) { showConversation(saved); return; }
    const sessionId = backendSessions.current.get(id);
    if (!sessionId) return;
    const controller = new AbortController();
    historyRequestRef.current = controller;
    loadingConversationRef.current = id;
    historyRef.current = [];
    progressRef.current = [];
    setMessages([]); setProgress([]); setDraft(""); setFailed(""); setError(""); setNotice("대화 기록을 불러오고 있어요."); setUncertain(false); setStreaming("");
    void restoreConversation(id, sessionId, controller, identityRef.current);
  }, [activeConversationId, archiveCurrentConversation, invalidateHistory, restoreConversation, showConversation, uncertain]);

  const deleteConversation = useCallback((id: string) => {
    // 답변을 받는 중인 대화는 지우지 않는다
    if (requestRef.current && id === activeConversationId) return;
    const sessionId = backendSessions.current.get(id);
    const remaining = conversations.filter(conversation => conversation.id !== id);
    if (remaining.length === conversations.length) return;
    if (id === activeConversationId) {
      const next = remaining[0];
      if (next) {
        selectConversation(next.id);
      } else {
        invalidateHistory();
        const fresh = createClientId();
        activeConversationRef.current = fresh;
        setActiveConversationId(fresh);
        remaining.push({ id: fresh, title: "새 대화" });
        historyRef.current = [];
        failedContextRef.current = undefined;
        progressRef.current = [];
        setMessages([]); setProgress([]); setDraft(""); setFailed(""); setError(""); setUncertain(false); setStreaming(""); setContext(undefined);
      }
      setNotice("대화 내역을 지웠어요.");
    }
    archivedConversations.current.delete(id);
    backendSessions.current.delete(id);
    setConversations(remaining);
    // 서버 기록 삭제가 실패해도 화면에서는 지운 상태를 유지한다
    if (sessionId) void deleteChatSession(sessionId).catch(() => undefined);
  }, [activeConversationId, conversations, invalidateHistory, selectConversation]);

  useEffect(() => {
    if (identityRef.current === identity) return;
    identityRef.current = identity;
    setChatIdentity(identity);
    requestVersion.current += 1;
    requestRef.current?.controller.abort();
    requestRef.current?.identityController.abort();
    statusRequestRef.current?.abort();
    historyRequestRef.current?.abort();
    loadingConversationRef.current = null;
    requestRef.current = null;
    statusRequestRef.current = null;
    pendingRef.current = "";
    backendSessions.current.clear();
    archivedConversations.current.clear();
    historyRef.current = [];
    failedContextRef.current = undefined;
    setActiveConversationId("initial-chat");
    activeConversationRef.current = "initial-chat";
    setConversations([{ id: "initial-chat", title: "새 대화" }]);
    setMessages([]);
    setDraft("");
    setContext(undefined);
    setStatus(null);
    setStatusLoading(memberStatus === "authenticated" || memberStatus === "loading");
    setStatusError(memberStatus === "unavailable" ? "로그인 상태를 확인하지 못했어요." : "");
    setPending("");
    setStreaming("");
    progressRef.current = [];
    setProgress([]);
    setFailed("");
    setError("");
    setNotice("");
    setUncertain(false);
    streamingRef.current = "";
  }, [identity, memberStatus]);

  useEffect(() => {
    if (memberStatus !== "authenticated" || identityRef.current !== identity) return;
    invalidateHistory();
    const controller = new AbortController(), expectedIdentity = identity;
    historyRequestRef.current = controller;
    void listChatSessions(controller.signal).then(sessions => {
      commitChatLoad(controller.signal, () => identityRef.current === expectedIdentity, () => {
        backendSessions.current.clear();
        if (!sessions.length) return;
        const rooms = sessions.map(room => ({ id: `member:${room.id}`, title: room.title || "새 대화" }));
        rooms.forEach((room, index) => backendSessions.current.set(room.id, sessions[index].id));
        const first = rooms[0];
        setConversations(rooms);
        setActiveConversationId(first.id);
        activeConversationRef.current = first.id;
        loadingConversationRef.current = first.id;
        setNotice("대화 기록을 불러오고 있어요.");
        void restoreConversation(first.id, sessions[0].id, controller, expectedIdentity);
      });
    }).catch(cause => {
      if (!controller.signal.aborted && identityRef.current === expectedIdentity) setError(cause instanceof Error ? cause.message : "대화방을 불러오지 못했어요.");
    });
    return () => controller.abort();
  }, [accountId, identity, invalidateHistory, memberStatus, restoreConversation]);

  const registerCourseTarget = useCallback((target: CourseTarget | null) => {
    courseTargetRef.current = target;
    setCourseTarget(target);
  }, []);
  const applyChatCourse = useCallback((course: ChatCourse, how: "replace" | "append") => {
    const target = courseTargetRef.current;
    if (!target) return;
    const had = target.stopCount, otherStadium = Boolean(course.stadiumCode && course.stadiumCode !== target.stadiumCode);
    const undo = target.apply(course, how);
    const message = !undo ? "이 코스를 지도에 담지 못했어요. 구장을 확인해 주세요."
      : otherStadium ? "구장을 바꾸고 추천 코스를 옆 지도에 그렸어요."
      : how === "append" ? "내 코스 뒤에 이어 담았어요."
      : had ? `옆 지도에 추천 코스를 그렸어요. 원래 담아둔 ${had}곳은 되돌리기로 복구할 수 있어요.`
      : "옆 지도에 추천 코스를 그렸어요. 순서는 내 코스에서 바꿀 수 있어요.";
    setAppliedCourses(current => new Map(current).set(course, { undo, message }));
  }, []);
  const undoChatCourse = useCallback((course: ChatCourse) => {
    appliedCoursesRef.current.get(course)?.undo?.();
    setAppliedCourses(current => new Map(current).set(course, { undo: null, message: "담기 전 코스로 되돌렸어요." }));
  }, []);

  const send = useCallback(async (text = draft, options?: { context?: ChatContext }) => {
    const selectedContext = options ? options.context : context;
    const content = text.trim();
    if (identityRef.current !== identity || !content || requestRef.current || content.length > MAX_MESSAGE_LENGTH) return;
    if (loadingConversationRef.current === activeConversationId) {
      setNotice("대화 기록을 불러온 뒤 보내 주세요.");
      return;
    }
    if (uncertain) {
      setError("서버 기록이 겹치지 않도록 새 대화에서 다시 보내 주세요.");
      return;
    }
    const controller = new AbortController();
    const identityController = new AbortController();
    const version = ++requestVersion.current;
    if (memberStatus !== "authenticated") { setError(memberStatus === "anonymous" ? "챗봇 질문은 로그인 후 이용할 수 있어요." : "로그인 상태를 확인한 뒤 다시 시도해 주세요."); return; }
    const mode = "member" as "member" | "guest";
    invalidateHistory();
    const active = { controller, identityController, version, mode, checkpoint: null as ChatCheckpoint | null, stop: null as ChatCheckpoint | null, wantsStop: false };
    requestRef.current = active;
    pendingRef.current = content;
    setPending(content);
    setDraft("");
    setError("");
    setNotice("");
    setFailed("");
    setStreaming("");
    progressRef.current = [];
    setProgress([]);
    streamingRef.current = "";
    failedContextRef.current = undefined;
    const userMessage: ChatMessage = { role: "user", content };
    const previous = historyRef.current;
    if (previous.length === 0) {
      setConversations(current => current.map(item => item.id === activeConversationId
        ? { ...item, title: content.replace(/\s+/g, " ").slice(0, 48) }
        : item));
    }
    try {
      // Keep complete exchanges, leaving one slot for this question.
      const sendRequest = mode === "member" ? sendChatMessage : sendGuestChatMessage;
      const reply = await sendRequest({
        messages: [...previous.slice(-(MAX_HISTORY_MESSAGES - 2)), userMessage],
        context: selectedContext,
        sessionId: mode === "member" ? backendSessions.current.get(activeConversationId) : undefined,
      }, controller.signal, {
        onCheckpoint: checkpoint => {
          if (requestRef.current !== active) return;
          active.checkpoint = checkpoint;
          if (active.wantsStop && !active.stop) { active.stop = checkpoint; controller.abort(); }
        },
        getStop: () => active.stop,
        isCurrent: () => requestRef.current === active && version === requestVersion.current,
        finalizeSignal: identityController.signal,
        onDelta: answer => {
          if (version !== requestVersion.current) return;
          streamingRef.current = answer;
          setStreaming(answer);
          if (mode === "guest") active.checkpoint = { turnId: "guest", receipt: "", prefix: answer };
        },
        onProgress: event => {
          if (version !== requestVersion.current) return;
          progressRef.current = reduceProgress(progressRef.current, event);
          setProgress(progressRef.current);
        },
      });
      if (version !== requestVersion.current) return;
      if (mode === "member" && reply.sessionId) backendSessions.current.set(activeConversationId, reply.sessionId);
      // 루트 작성 화면이면 챗봇이 짠 코스를 옆 지도에 바로 그린다 (카드에서 되돌리기 가능)
      if (reply.course && reply.completionStatus !== "stopped" && courseTargetRef.current) applyChatCourse(reply.course, "replace");
      const finalProgress = reply.completionStatus === "stopped" ? settleProgress(progressRef.current) : progressRef.current;
      const assistant = { role: "assistant" as const, content: reply.reply, ...(reply.course ? { course: reply.course } : {}), progress: finalProgress, turnStatus: reply.completionStatus };
      const next: ChatMessage[] = [...previous, userMessage, ...(reply.reply || progressRef.current.length ? [assistant] : [])];
      historyRef.current = next;
      setMessages(next);
      progressRef.current = [];
      setProgress([]);
      setStatus({ provider: reply.provider, model: reply.model, ready: reply.ready });
      setNotice(reply.completionStatus === "stopped" ? "받은 답변까지만 보관했어요." : "");
      setUncertain(false);
    } catch (cause) {
      if (version !== requestVersion.current) return;
      if (mode === "member" && cause instanceof ChatClientError && cause.sessionId) backendSessions.current.set(activeConversationId, cause.sessionId);
      const deliveryUncertain = mode === "member" && cause instanceof ChatClientError && cause.uncertain;
      const received = streamingRef.current;
      const finalProgress = settleProgress(progressRef.current);
      if (deliveryUncertain && (received || progressRef.current.length)) {
        if (progressRef.current.length) setMessages([...previous, userMessage, { role: "assistant", content: received, progress: finalProgress }]);
        else setMessages([...previous, userMessage, { role: "assistant", content: received }]);
        progressRef.current = [];
        setProgress([]);
      } else if (progressRef.current.length) {
        progressRef.current = finalProgress;
        setProgress(finalProgress);
      }
      setUncertain(deliveryUncertain);
      setFailed(deliveryUncertain ? "" : content);
      failedContextRef.current = deliveryUncertain ? undefined : selectedContext;
      setDraft(current => current.trim() ? current : content);
      setError(deliveryUncertain
        ? `${cause.message}${received ? " 받은 답변은 저장되지 않았어요." : ""} 기록이 겹치지 않도록 새 대화에서 다시 보내 주세요.`
        : cause instanceof Error ? cause.message : "답변을 가져오지 못했어요. 다시 시도해 주세요.");
    } finally {
      if (version === requestVersion.current) {
        requestRef.current = null;
        pendingRef.current = "";
        setPending("");
        setStreaming("");
        streamingRef.current = "";
      }
    }
  }, [activeConversationId, applyChatCourse, context, draft, identity, invalidateHistory, memberStatus, uncertain]);

  const openCourseInWriter = useCallback((course: ChatCourse) => {
    pendingCourseRef.current = course;
    setPopupRequested(false);
    router.push(`/routes/new${course.stadiumCode ? `?stadium=${encodeURIComponent(course.stadiumCode)}` : ""}`);
  }, [router]);
  const takePendingCourse = useCallback(() => {
    const course = pendingCourseRef.current;
    pendingCourseRef.current = null;
    return course;
  }, []);

  const openChat = useCallback((initialMessage?: string, nextContext?: ChatContext) => {
    expandChat();
    if (nextContext) setContext(nextContext);
    if (requestRef.current) {
      if (initialMessage?.trim()) {
        changeDraft(initialMessage.slice(0, MAX_MESSAGE_LENGTH));
        setNotice("지금 답변이 끝나면 아래에 준비한 질문을 보낼 수 있어요.");
      }
      return;
    }
    if (initialMessage?.trim()) void send(initialMessage.slice(0, MAX_MESSAGE_LENGTH), { context: nextContext ?? context });
  }, [changeDraft, context, expandChat, send]);

  useEffect(() => {
    if (isChatPage || !restorePageRef.current) return;
    restorePageRef.current = false;
    const frame = requestAnimationFrame(() => window.scrollTo({ top: returnPageRef.current.scrollY, behavior: "instant" }));
    return () => cancelAnimationFrame(frame);
  }, [isChatPage]);

  useEffect(() => {
    if (!isChatPage && !popupOpen && !hasEmbeddedChat) return;
    statusRequestRef.current?.abort();
    if (memberStatus !== "authenticated") return;
    const controller = new AbortController();
    statusRequestRef.current = controller;
    void loadStatus(controller);
    return () => controller.abort();
  }, [accountId, hasEmbeddedChat, isChatPage, popupOpen, loadStatus, memberStatus]);

  useEffect(() => () => {
    requestVersion.current += 1;
    requestRef.current?.controller.abort();
    requestRef.current?.identityController.abort();
    statusRequestRef.current?.abort();
    historyRequestRef.current?.abort();
  }, []);

  const visibleStatus = memberStatus === "authenticated" ? status : null;
  const visibleStatusLoading = memberStatus === "loading" || (memberStatus === "authenticated" && statusLoading);
  const visibleStatusError = memberStatus === "unavailable" ? "로그인 상태를 확인하지 못했어요." : memberStatus === "authenticated" ? statusError : "";

  return (
    <ChatControlsContext.Provider value={{
      openChat, onExpand: expandChat, onMinimize: minimizeChat, onClosePopup: closePopup,
      messages: identityChanged ? [] : messages,
      draft: identityChanged ? "" : draft,
      context: identityChanged ? undefined : context,
      status: visibleStatus,
      statusLoading: visibleStatusLoading,
      statusError: visibleStatusError,
      pending: identityChanged ? "" : pending,
      uncertain: identityChanged ? false : uncertain,
      streaming: identityChanged ? "" : streaming,
      progress: identityChanged ? [] : progress,
      failed: identityChanged ? "" : failed,
      error: identityChanged ? "" : error,
      notice: identityChanged ? "" : notice,
      conversations: identityChanged ? [{ id: "initial-chat", title: "새 대화" }] : conversations,
      activeConversationId: identityChanged ? "initial-chat" : activeConversationId,
      onDraftChange: changeDraft, onRefreshStatus: () => void refreshStatus(),
      onSend: () => void send(), onRetry: () => void send(failed, { context: failedContextRef.current }),
      onCancel: cancelRequest, onReset: resetChat,
      onSuggestion: (text, intent) => { changeDraft(text); setContext(current => ({ ...current, intent })); },
      onSelectConversation: selectConversation,
      onDeleteConversation: deleteConversation,
      onContextChange: setContext,
      courseTarget, registerCourseTarget, openCourseInWriter, takePendingCourse,
      appliedCourses, applyChatCourse, undoChatCourse,
    }}>
      {children}
      {!popupOpen && <button ref={topButtonRef} type="button" className="scroll-to-top" aria-label="맨 위로 이동" title="맨 위로 이동" onClick={() => window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" })}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 11 6-6 6 6M12 5v14" /></svg>
      </button>}
      {popupOpen && <ChatPopup />}
    </ChatControlsContext.Provider>
  );
}
