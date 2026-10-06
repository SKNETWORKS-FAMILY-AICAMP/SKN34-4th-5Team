import { memberError, memberFetch } from "../member-auth-request";
import { isRecord, parseChatRequest } from "./validation";
import { parseChatCourse } from "./course";
import { isUuid, parseProgressEvent } from "./progress";
import type { ChatCourse, ChatProgressEvent, ChatReply, ChatRequest, ChatStatus } from "./types";
import { MAX_REPLY_LENGTH } from "./types";
import type {
  ChatCourseMetadataDto,
  ChatFinalizeResponseDto,
  ChatMessageDto,
  ChatNonStreamResponseDto,
  ChatSessionDto,
  ChatTurnHistoryDto,
  ChatTurnPageDto,
  GuestChatDoneEventDto,
} from "./wire";

const REQUEST_TIMEOUT_MS = 55_000;
const MEMBER_STATUS: ChatStatus = { provider: "backend", model: "팀 챗봇", ready: true };
export const GUEST_STATUS: ChatStatus = { provider: "guest", model: "팀 챗봇", ready: true };

export type ChatCheckpoint = { turnId: string; receipt: string; prefix: string };
export type ChatStreamCallbacks = {
  onDelta?: (answer: string, checkpoint?: ChatCheckpoint) => void;
  onCheckpoint?: (checkpoint: ChatCheckpoint) => void;
  onProgress?: (progress: ChatProgressEvent) => void;
  getStop?: () => ChatCheckpoint | null;
  isCurrent?: () => boolean;
  finalizeSignal?: AbortSignal;
};

export class ChatClientError extends Error {
  constructor(message: string, public status: number, public uncertain = false, public sessionId?: number) { super(message); }
}

function fallback(status: number) {
  if (status === 401) return "팀 계정 로그인을 확인해 주세요.";
  if (status === 403) return "이 대화를 이용할 권한이 없어요.";
  if (status === 404) return "채팅방을 찾지 못했어요. 새 대화를 시작해 주세요.";
  if (status === 409) return "더 최신 대화가 있어 이 답변을 저장하지 못했어요.";
  if (status === 429) return "요청이 많아요. 잠시 후 다시 시도해 주세요.";
  if (status >= 500) return "챗봇 서버에서 요청을 처리하지 못했어요.";
  return "입력 내용을 확인해 주세요.";
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function request<T>(
  path: string,
  init: RequestInit,
  signal: AbortSignal | undefined,
  fetcher: (path: string, init: RequestInit) => Promise<Response>,
  read: (response: Response) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
  const mutating = init.method !== "GET";
  try {
    const response = await fetcher(path, { ...init, cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      const data = await readJson(response).catch(() => null);
      throw new ChatClientError(
        response.status >= 500 ? fallback(response.status) : memberError(data, fallback(response.status)),
        response.status,
        response.status >= 500 && mutating,
      );
    }
    return await read(response);
  } catch (error) {
    if (error instanceof ChatClientError) throw error;
    if (error instanceof SyntaxError) throw new ChatClientError("응답을 읽지 못했어요.", 502, mutating);
    if (timedOut) throw new ChatClientError("응답 시간이 지났지만 서버 처리 여부는 확인할 수 없어요.", 504, mutating);
    if (controller.signal.aborted) throw new ChatClientError("요청이 중단됐어요.", 499, mutating);
    throw new ChatClientError("연결이 끊겨 서버 처리 여부를 확인할 수 없어요.", 502, mutating);
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

const memberRequest = <T>(path: string, init: RequestInit, signal: AbortSignal | undefined, read: (response: Response) => Promise<T>) =>
  request(path, init, signal, memberFetch, read);
const guestRequest = <T>(path: string, init: RequestInit, signal: AbortSignal | undefined, read: (response: Response) => Promise<T>) =>
  request(path, init, signal, fetch, read);

function parseCheckpoint(value: Record<string, unknown>, prefix: string, turnId?: string): ChatCheckpoint {
  if (typeof value.turn_id !== "string" || !value.turn_id || typeof value.receipt !== "string" || !value.receipt || (turnId && value.turn_id !== turnId)) {
    throw new ChatClientError("체크포인트 형식이 올바르지 않아요.", 502, true);
  }
  return { turnId: value.turn_id, receipt: value.receipt, prefix };
}

function courseMetadata(value: Record<string, unknown>): ChatCourseMetadataDto {
  return {
    ...(Array.isArray(value.places) ? { places: value.places } : {}),
    ...(value.coursePayload === null || isRecord(value.coursePayload) ? { coursePayload: value.coursePayload } : {}),
    ...(typeof value.route === "string" ? { route: value.route } : {}),
  } as ChatCourseMetadataDto;
}

async function finalizeMemberTurn(sessionId: number, checkpoint: ChatCheckpoint, status: "completed" | "stopped", callbacks: ChatStreamCallbacks) {
  if (callbacks.isCurrent && !callbacks.isCurrent()) throw new ChatClientError("계정이 변경되어 저장을 중단했어요.", 409);
  const value = await memberRequest(`/api/chat/turns/${checkpoint.turnId}/finalize/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ receipt: checkpoint.receipt, prefix: checkpoint.prefix, status }),
  }, callbacks.finalizeSignal, readJson);
  if (!isRecord(value) || value.turn_id !== checkpoint.turnId || value.session_id !== sessionId ||
      (value.status !== "completed" && value.status !== "stopped") || typeof value.user_message_id !== "number" ||
      (value.assistant_message_id !== null && typeof value.assistant_message_id !== "number") ||
      typeof value.assistant_message !== "string") {
    throw new ChatClientError("저장 확인 응답이 올바르지 않아요.", 502, true, sessionId);
  }
  const finalized = value as ChatFinalizeResponseDto;
  return {
    ...MEMBER_STATUS,
    sessionId,
    reply: finalized.assistant_message,
    completionStatus: finalized.status,
    turnId: checkpoint.turnId,
    userMessageId: finalized.user_message_id,
    assistantMessageId: finalized.assistant_message_id,
  } satisfies ChatReply;
}

async function readMemberStream(response: Response, sessionId: number, callbacks: ChatStreamCallbacks): Promise<ChatReply> {
  if (!response.body || !response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream")) {
    throw new ChatClientError("스트림 응답을 확인하지 못했어요.", 502, true, sessionId);
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "", answer = "", checkpoint: ChatCheckpoint | null = null, progressTurn = "", lastProgressSequence = 0, done = false, metadata: ChatCourseMetadataDto = {}, course: ChatCourse | undefined;
  const progressFrames = new Map<number, string>();
  const consume = (frame: string) => {
    const [eventLine, ...lines] = frame.split(/\r?\n/);
    const event = eventLine?.startsWith("event:") ? eventLine.slice(6).trim() : "";
    const raw = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
    let value: unknown;
    try { value = JSON.parse(raw); } catch { throw new ChatClientError("스트림 응답 형식이 올바르지 않아요.", 502, true, sessionId); }
    if (!isRecord(value) || done) throw new ChatClientError("스트림 응답 순서가 올바르지 않아요.", 502, true, sessionId);
    if (event === "checkpoint") {
      if (checkpoint) throw new ChatClientError("체크포인트 순서가 올바르지 않아요.", 502, true, sessionId);
      checkpoint = parseCheckpoint(value, "");
      if (progressTurn && progressTurn !== checkpoint.turnId) throw new ChatClientError("진행 상태의 대화 정보가 다릅니다.", 502, true, sessionId);
      callbacks.onCheckpoint?.(checkpoint);
      return;
    }
    if (event === "progress") {
      const progress = parseProgressEvent(value, checkpoint?.turnId ?? (progressTurn || undefined));
      if (!progress) throw new ChatClientError("진행 상태 형식이 올바르지 않아요.", 502, true, sessionId);
      const fingerprint = JSON.stringify(value), seen = progressFrames.get(progress.sequenceNo);
      if (seen) {
        if (seen !== fingerprint) throw new ChatClientError("진행 상태 순서가 충돌했어요.", 502, true, sessionId);
        return;
      }
      if (progress.sequenceNo <= lastProgressSequence) throw new ChatClientError("진행 상태 순서가 올바르지 않아요.", 502, true, sessionId);
      progressTurn = progress.turnId; lastProgressSequence = progress.sequenceNo; progressFrames.set(progress.sequenceNo, fingerprint);
      callbacks.onProgress?.(progress);
      return;
    }
    if (event === "delta") {
      if (!checkpoint || typeof value.text !== "string" || !value.text || answer.length + value.text.length > MAX_REPLY_LENGTH) {
        throw new ChatClientError("답변이 비어 있거나 너무 길어요.", 502, true, sessionId);
      }
      answer += value.text;
      checkpoint = parseCheckpoint(value, answer, checkpoint.turnId);
      callbacks.onCheckpoint?.(checkpoint);
      callbacks.onDelta?.(answer, checkpoint);
      return;
    }
    if (event === "error") {
      const detail = typeof value.detail === "string" && value.detail.length <= 200 ? value.detail : fallback(502);
      throw new ChatClientError(detail, 502, false, sessionId);
    }
    if (event === "done") {
      if (!checkpoint || !answer.trim()) throw new ChatClientError("최종 답변을 확인하지 못했어요.", 502, true, sessionId);
      checkpoint = parseCheckpoint(value, answer, checkpoint.turnId);
      metadata = courseMetadata(value);
      callbacks.onCheckpoint?.(checkpoint);
      course = parseChatCourse(value);
      done = true;
      return;
    }
    throw new ChatClientError("알 수 없는 스트림 응답을 받았어요.", 502, true, sessionId);
  };

  try {
    while (!done) {
      const next = await reader.read();
      buffer += decoder.decode(next.value, { stream: !next.done });
      let boundary;
      while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
        const frame = buffer.slice(0, boundary), separator = buffer.slice(boundary).startsWith("\r\n\r\n") ? 4 : 2;
        buffer = buffer.slice(boundary + separator);
        if (frame.trim()) consume(frame);
      }
      if (buffer.length > MAX_REPLY_LENGTH * 4) throw new ChatClientError("스트림 응답이 너무 길어요.", 502, true, sessionId);
      if (next.done) break;
    }
    if (!done || buffer.trim() || !checkpoint) throw new ChatClientError("답변 완료를 확인하지 못했어요.", 502, true, sessionId);
    const stopped = callbacks.getStop?.();
    let result = await finalizeMemberTurn(sessionId, stopped ?? checkpoint, stopped ? "stopped" : "completed", callbacks);
    const racedStop = callbacks.getStop?.();
    if (result.completionStatus === "completed" && racedStop) result = await finalizeMemberTurn(sessionId, racedStop, "stopped", callbacks);
    const withMetadata = { ...result, ...metadata };
    // 중간에 멈춘 답에는 코스 카드를 붙이지 않는다 (본문과 코스가 어긋난다)
    return result.completionStatus === "completed" && course ? { ...withMetadata, course } : withMetadata;
  } catch (error) {
    const stopped = callbacks.getStop?.();
    if (stopped) return finalizeMemberTurn(sessionId, stopped, "stopped", callbacks);
    throw error;
  } finally { await reader.cancel().catch(() => undefined); }
}

async function readGuestStream(response: Response, callbacks: ChatStreamCallbacks): Promise<ChatReply> {
  if (!response.body || !response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream")) {
    throw new ChatClientError("스트림 응답을 확인하지 못했어요.", 502);
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "", answer = "", progressTurn = "", lastProgressSequence = 0, done = false, metadata: ChatCourseMetadataDto = {}, course: ChatCourse | undefined;
  const progressFrames = new Map<number, string>();
  callbacks.onCheckpoint?.({ turnId: "guest", receipt: "", prefix: "" });
  const consume = (frame: string) => {
    const [eventLine, ...lines] = frame.split(/\r?\n/), event = eventLine?.slice(6).trim();
    const raw = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
    let value: unknown;
    try { value = JSON.parse(raw); } catch { throw new ChatClientError("스트림 응답 형식이 올바르지 않아요.", 502); }
    if (!isRecord(value) || done) throw new ChatClientError("스트림 응답 순서가 올바르지 않아요.", 502);
    if (event === "progress") {
      const progress = parseProgressEvent(value, progressTurn || undefined);
      if (!progress) throw new ChatClientError("진행 상태 형식이 올바르지 않아요.", 502);
      const fingerprint = JSON.stringify(value), seen = progressFrames.get(progress.sequenceNo);
      if (seen) {
        if (seen !== fingerprint) throw new ChatClientError("진행 상태 순서가 충돌했어요.", 502);
        return;
      }
      if (progress.sequenceNo <= lastProgressSequence) throw new ChatClientError("진행 상태 순서가 올바르지 않아요.", 502);
      progressTurn = progress.turnId; lastProgressSequence = progress.sequenceNo; progressFrames.set(progress.sequenceNo, fingerprint);
      callbacks.onProgress?.(progress);
      return;
    }
    if (event === "delta") {
      if (typeof value.text !== "string" || !value.text || answer.length + value.text.length > MAX_REPLY_LENGTH) throw new ChatClientError("답변이 너무 길어요.", 502);
      answer += value.text;
      callbacks.onCheckpoint?.({ turnId: "guest", receipt: "", prefix: answer });
      callbacks.onDelta?.(answer);
      return;
    }
    if (event === "done") {
      if (!answer.trim() || value.assistant_message !== answer) throw new ChatClientError("최종 답변을 확인하지 못했어요.", 502);
      metadata = courseMetadata(value as GuestChatDoneEventDto & Record<string, unknown>);
      course = parseChatCourse(value);
      done = true; return;
    }
    if (event === "error") throw new ChatClientError(typeof value.detail === "string" && value.detail.length <= 200 ? value.detail : fallback(502), 502);
    throw new ChatClientError("알 수 없는 스트림 응답을 받았어요.", 502);
  };
  try {
    while (!done) {
      const next = await reader.read();
      buffer += decoder.decode(next.value, { stream: !next.done });
      let boundary;
      while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
        const frame = buffer.slice(0, boundary), separator = buffer.slice(boundary).startsWith("\r\n\r\n") ? 4 : 2;
        buffer = buffer.slice(boundary + separator); if (frame.trim()) consume(frame);
      }
      if (buffer.length > MAX_REPLY_LENGTH * 4) throw new ChatClientError("스트림 응답이 너무 길어요.", 502);
      if (next.done) break;
    }
    if (!done || buffer.trim()) throw new ChatClientError("답변 완료를 확인하지 못했어요.", 502);
    const stop = callbacks.getStop?.();
    return { ...GUEST_STATUS, ...metadata, reply: stop ? (stop.prefix.trim() ? stop.prefix : "") : answer, completionStatus: stop ? "stopped" : "completed", ...(!stop && course ? { course } : {}) };
  } catch (error) {
    const stop = callbacks.getStop?.();
    if (stop) return { ...GUEST_STATUS, reply: stop.prefix.trim() ? stop.prefix : "", completionStatus: "stopped" };
    throw error;
  } finally { await reader.cancel().catch(() => undefined); }
}

export async function listChatSessions(signal?: AbortSignal): Promise<ChatSessionDto[]> {
  const sessions = await memberRequest("/api/chat/sessions/", { method: "GET" }, signal, readJson);
  if (!Array.isArray(sessions) || sessions.some(item => !isRecord(item) || !Number.isSafeInteger(item.id) || typeof item.title !== "string")) {
    throw new ChatClientError("채팅방 목록 응답을 확인하지 못했어요.", 502);
  }
  return sessions as ChatSessionDto[];
}

export async function createChatSession(title: string, signal?: AbortSignal): Promise<ChatSessionDto> {
  const room = await memberRequest("/api/chat/sessions/", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
  }, signal, readJson);
  if (!isRecord(room) || !Number.isSafeInteger(room.id) || Number(room.id) < 1 || typeof room.title !== "string") {
    throw new ChatClientError("채팅방 생성 응답을 확인하지 못했어요.", 502);
  }
  return room as ChatSessionDto;
}

export async function renameChatSession(sessionId: number, title: string, signal?: AbortSignal): Promise<ChatSessionDto> {
  const room = await memberRequest(`/api/chat/sessions/${sessionId}/`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
  }, signal, readJson);
  if (!isRecord(room) || room.id !== sessionId || typeof room.title !== "string") throw new ChatClientError("채팅방 수정 응답을 확인하지 못했어요.", 502, true, sessionId);
  return room as ChatSessionDto;
}

export async function deleteChatSession(sessionId: number, signal?: AbortSignal): Promise<void> {
  const result = await memberRequest(`/api/chat/sessions/${sessionId}/`, { method: "DELETE" }, signal, readJson);
  if (result !== null) throw new ChatClientError("채팅방 삭제 응답을 확인하지 못했어요.", 502, true, sessionId);
}

export async function fetchChatHistory(sessionId: number, signal?: AbortSignal): Promise<ChatMessageDto[]> {
  const messages = await memberRequest(`/api/chat/sessions/${sessionId}/messages/`, { method: "GET" }, signal, readJson);
  if (!Array.isArray(messages) || messages.some(item => !isRecord(item) || typeof item.content !== "string")) throw new ChatClientError("대화 기록 응답을 확인하지 못했어요.", 502, false, sessionId);
  return messages as ChatMessageDto[];
}

function turnPagePath(next: string, sessionId: number): string {
  const origin = window.location?.origin ?? "http://localhost";
  const expected = `/api/chat/sessions/${sessionId}/turns/`;
  let url: URL;
  try { url = new URL(next, `${origin}${expected}`); } catch { throw new ChatClientError("대화 진행 기록 다음 페이지 주소가 올바르지 않아요.", 502, false, sessionId); }
  const page = url.searchParams.get("page");
  if (url.origin !== origin || url.pathname !== expected || url.username || url.password || url.hash ||
      [...url.searchParams.keys()].some(key => key !== "page") || !/^[1-9]\d*$/.test(page ?? "") || !Number.isSafeInteger(Number(page))) {
    throw new ChatClientError("대화 진행 기록 다음 페이지 주소가 올바르지 않아요.", 502, false, sessionId);
  }
  return `${url.pathname}${url.search}`;
}

function parseTurnPage(value: unknown, sessionId: number): ChatTurnPageDto {
  if (!isRecord(value) || !Number.isSafeInteger(value.count) || Number(value.count) < 0 ||
      (value.next !== null && typeof value.next !== "string") || (value.previous !== null && typeof value.previous !== "string") || !Array.isArray(value.results)) {
    throw new ChatClientError("대화 진행 기록 응답을 확인하지 못했어요.", 502, false, sessionId);
  }
  for (const turn of value.results) {
    if (!isRecord(turn) || !isUuid(turn.id) || typeof turn.question !== "string" || !turn.question.trim() || turn.question.length > 2000 ||
        !["pending", "completed", "stopped", "failed"].includes(String(turn.status)) || !Number.isSafeInteger(turn.base_sequence) || Number(turn.base_sequence) < 0 ||
        (turn.human_message_id !== null && (!Number.isSafeInteger(turn.human_message_id) || Number(turn.human_message_id) < 1)) ||
        (turn.assistant_message_id !== null && (!Number.isSafeInteger(turn.assistant_message_id) || Number(turn.assistant_message_id) < 1)) || !Array.isArray(turn.progress)) {
      throw new ChatClientError("대화 진행 기록 응답을 확인하지 못했어요.", 502, false, sessionId);
    }
    let sequence = 0;
    for (const raw of turn.progress) {
      const progress = parseProgressEvent(raw, turn.id);
      if (!progress || progress.sequenceNo <= sequence) throw new ChatClientError("대화 진행 기록 순서가 올바르지 않아요.", 502, false, sessionId);
      sequence = progress.sequenceNo;
    }
  }
  return value as unknown as ChatTurnPageDto;
}

export async function fetchChatTurns(sessionId: number, signal?: AbortSignal): Promise<ChatTurnHistoryDto[]> {
  const turns: ChatTurnHistoryDto[] = [];
  let path = `/api/chat/sessions/${sessionId}/turns/?page=1`;
  const seen = new Set<string>();
  let expectedCount: number | undefined;
  do {
    if (seen.has(path)) throw new ChatClientError("대화 진행 기록 페이지가 반복돼요.", 502, false, sessionId);
    seen.add(path);
    const page = parseTurnPage(await memberRequest(path, { method: "GET" }, signal, readJson), sessionId);
    if (expectedCount !== undefined && page.count !== expectedCount) throw new ChatClientError("대화 진행 기록 개수가 올바르지 않아요.", 502, false, sessionId);
    expectedCount = page.count;
    turns.push(...page.results);
    path = page.next ? turnPagePath(page.next, sessionId) : "";
    if (turns.length > page.count) throw new ChatClientError("대화 진행 기록 개수가 올바르지 않아요.", 502, false, sessionId);
  } while (path);
  if (turns.length !== expectedCount) throw new ChatClientError("대화 진행 기록 개수가 올바르지 않아요.", 502, false, sessionId);
  return turns;
}

export async function sendNonStreamChatMessage(sessionId: number, content: string, signal?: AbortSignal): Promise<ChatNonStreamResponseDto> {
  const result = await memberRequest(`/api/chat/sessions/${sessionId}/messages/`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ content }),
  }, signal, readJson);
  if (!isRecord(result) || result.session_id !== sessionId || result.status !== "completed" || typeof result.assistant_message !== "string") {
    throw new ChatClientError("채팅 응답을 확인하지 못했어요.", 502, true, sessionId);
  }
  return result as ChatNonStreamResponseDto;
}

export async function getChatStatus(signal?: AbortSignal): Promise<ChatStatus> {
  await listChatSessions(signal);
  return MEMBER_STATUS;
}

// The backend reads the selected stadium and map origin from these leading tags (llm/rag/pipeline.py).
export function contextPrefix(context?: ChatRequest["context"]) {
  if (!context) return "";
  const tags = [
    ...(context.stadium ? [`[선택한 구장: ${context.stadium}]`] : []),
    ...(context.origin ? [`[출발지: ${context.origin.lat.toFixed(6)},${context.origin.lng.toFixed(6)}]`] : []),
  ];
  return tags.length ? `${tags.join("\n")}\n` : "";
}

function parseModelRequest(body: ChatRequest): ChatRequest {
  return parseChatRequest({
    ...body,
    messages: body.messages.filter(message => !(
      message.role === "assistant" && !message.content.trim() &&
      (message.turnStatus === "pending" || message.turnStatus === "stopped" || message.turnStatus === "failed" || Array.isArray(message.progress))
    )),
  });
}

export async function sendChatMessage(body: ChatRequest, signal?: AbortSignal, callbacks: ChatStreamCallbacks = {}): Promise<ChatReply> {
  const input = parseModelRequest(body), question = input.messages.at(-1)!.content;
  let sessionId = input.sessionId;
  if (!sessionId) {
    sessionId = (await createChatSession(question.slice(0, 80), signal)).id;
  }
  const content = `${contextPrefix(input.context)}${question}`;
  try {
    return await memberRequest(`/api/chat/sessions/${sessionId}/messages/`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content }),
    }, signal, response => readMemberStream(response, sessionId!, callbacks));
  } catch (error) {
    if (error instanceof ChatClientError && error.sessionId === undefined) error.sessionId = sessionId;
    throw error;
  }
}

export async function sendGuestChatMessage(body: ChatRequest, signal?: AbortSignal, callbacks: ChatStreamCallbacks = {}): Promise<ChatReply> {
  const input = parseModelRequest(body);
  const messages = input.messages.map((message, index) => ({
    ...message,
    content: index === input.messages.length - 1 ? `${contextPrefix(input.context)}${message.content}` : message.content,
  }));
  return guestRequest("/api/chat/guest/", {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ messages }),
  }, signal, response => readGuestStream(response, callbacks));
}
