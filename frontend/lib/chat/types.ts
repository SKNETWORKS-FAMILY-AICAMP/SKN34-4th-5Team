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
export type ChatToolStatus = "running" | "completed" | "failed";
// serializer/message.py _public_tool(): {id, tool_name, status}. No arguments/results/metadata.
export type ChatToolCall = { id: string; toolName: string; status: ChatToolStatus };
export type ChatMessageStatus = "pending" | "completed" | "failed" | "stopped";
// id·status·tools 는 서버에 저장된 메시지에만 있다 (id 는 서버가 만든 양의 정수). course 는 화면 표시용이다.
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  id?: number;
  status?: ChatMessageStatus;
  course?: ChatCourse;
  tools?: ChatToolCall[];
};
export type ChatOrigin = { lat: number; lng: number };
// origin: 코스 작성 화면에서 지도에 찍은 출발지. 백엔드 코스 챗봇이 이 지점부터 이어서 코스를 짠다.
export type ChatContext = { stadium?: string; intent?: "route" | "baseball" | "stadium"; origin?: ChatOrigin };
export type ChatRequest = { messages: ChatMessage[]; sessionId?: string; context?: ChatContext };
export type ChatStatus = { provider: "demo" | "openai" | "backend" | "guest"; model: string; ready: boolean };
export type ChatReply = ChatStatus & {
  reply: string;
  sessionId?: string;
  assistantMessageId?: number;
  tools?: ChatToolCall[];
};

export const MAX_MESSAGE_LENGTH = 2000;
export const MAX_HISTORY_MESSAGES = 12;
export const MAX_REPLY_LENGTH = 8000;
export const MAX_REQUEST_BYTES = 64000;

// Live-only view of one streaming turn, in SSE arrival order. Never persisted: done.assistant_message replaces it.
export type ChatTimelineItem = { kind: "text"; text: string } | { kind: "tools"; tools: ChatToolCall[] };
/** Appends a raw delta or tool event: consecutive deltas/tools coalesce, a known tool id updates in place. */
export function appendTimeline(items: ChatTimelineItem[], event: string | ChatToolCall): ChatTimelineItem[] {
  const last = items.at(-1);
  if (typeof event === "string") {
    if (!event) return items;
    return last?.kind === "text" ? [...items.slice(0, -1), { kind: "text", text: last.text + event }] : [...items, { kind: "text", text: event }];
  }
  if (items.some(item => item.kind === "tools" && item.tools.some(tool => tool.id === event.id))) {
    return items.map(item => item.kind === "tools" ? { kind: "tools", tools: item.tools.map(tool => tool.id === event.id ? event : tool) } : item);
  }
  return last?.kind === "tools" ? [...items.slice(0, -1), { kind: "tools", tools: [...last.tools, event] }] : [...items, { kind: "tools", tools: [event] }];
}
