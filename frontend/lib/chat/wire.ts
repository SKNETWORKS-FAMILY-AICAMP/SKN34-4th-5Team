// Hand-typed wire DTOs for /api/v2/chat/ (backend/llm/serializer, views/sse.py).
// contracts/openapi.yaml and lib/api/schema.d.ts still describe the retired turns/finalize API.
import type { ChatContext } from "./types";

export type ChatMessageStatus = "pending" | "completed" | "failed" | "stopped";

export type ChatSessionDto = { id: string; title: string; created_at: string; updated_at: string };
export type ChatToolCallDto = { id: number; tool_name: string; status: string; created_at: string };
export type ChatMessageDto = {
  id: number;
  sequence_no: number;
  role: "user" | "assistant";
  content: string;
  status: ChatMessageStatus;
  tools: ChatToolCallDto[];
  created_at: string;
  updated_at: string;
};

export type ChatMessageRequestDto = { content: string; context?: ChatContext };
export type ChatMessageUpdateRequestDto = ChatMessageRequestDto & { message_id: number };
export type ChatMessageDeleteRequestDto = { message_id: number };

// done.message_id is the saved assistant message id, serialized as a string of digits.
export type ChatSseEvent =
  | { event: "delta"; data: { text: string } }
  | { event: "done"; data: { message_id: string; assistant_message: string } }
  | { event: "error"; data: { detail: string } };
