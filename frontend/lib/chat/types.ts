export type ChatCoursePhase = "BEFORE" | "GAME" | "AFTER";
export type ChatCoursePlace = {
  phase: ChatCoursePhase; name: string; lat: number; lng: number;
  // STAY·WALK·INDOOR 는 백엔드가 카카오 실시간 조회로 더한 종류 (RAG 에 없는 숙박·산책·실내놀거리)
  category: "FOOD" | "CAFE" | "SPOT" | "STADIUM" | "STAY" | "WALK" | "INDOOR"; placeId?: string; address?: string;
  reason?: string; time?: string; stayMin?: number;
};
/** 챗봇이 짠 코스. 백엔드 done 이벤트의 places·travel·coursePayload 에서 온다 (lib/chat/course.ts). */
export type ChatCourse = {
  places: ChatCoursePlace[];
  stadiumCode?: string;
  travelMode?: "walk" | "car" | "transit";
  travelLabel?: string;
  summary?: string;
  notes: string[];
  title?: string;
  content?: string;
};
export type ChatProgressStatus = "started" | "completed" | "failed" | "interrupted" | "unknown";
export type ChatProgressEvent = {
  turnId: string;
  sequenceNo: number;
  operationId: string;
  parentOperationId: string | null;
  kind: "phase" | "retrieval" | "tool";
  status: ChatProgressStatus;
  label: string;
  createdAt: string;
  toolName: string | null;
  toolCallId?: string | null;
  arguments?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  truncated?: boolean;
  summary: Record<string, unknown> | null;
};
export type ChatProgressOperation = Omit<ChatProgressEvent, "sequenceNo"> & {
  startedAt?: string;
  startSequenceNo?: number;
  sequenceNo: number;
};
export type ChatTurnStatus = "pending" | "completed" | "stopped" | "failed";
// course·progress 는 화면 표시용이다. parseChatRequest 가 서버로 보낼 때 role·content 만 남긴다.
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  id?: number;
  course?: ChatCourse;
  progress?: ChatProgressOperation[];
  turnStatus?: ChatTurnStatus;
};
export type ChatOrigin = { lat: number; lng: number };
// origin: 코스 작성 화면에서 지도에 찍은 출발지. 백엔드 코스 챗봇이 이 지점부터 이어서 코스를 짠다.
export type ChatContext = { stadium?: string; intent?: "route" | "baseball" | "stadium"; origin?: ChatOrigin };
export type ChatRequest = { messages: ChatMessage[]; sessionId?: number; context?: ChatContext };
export type ChatStatus = { provider: "demo" | "openai" | "backend" | "guest"; model: string; ready: boolean };
export type ChatReply = ChatStatus & ChatCourseMetadataDto & {
  reply: string;
  sessionId?: number;
  completionStatus?: "completed" | "stopped";
  turnId?: string;
  userMessageId?: number;
  assistantMessageId?: number | null;
  course?: ChatCourse;
};

export const MAX_MESSAGE_LENGTH = 2000;
export const MAX_HISTORY_MESSAGES = 12;
export const MAX_REPLY_LENGTH = 8000;
export const MAX_REQUEST_BYTES = 64000;
import type { ChatCourseMetadataDto } from "./wire";
