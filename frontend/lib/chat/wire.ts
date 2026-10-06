import type { components } from "../api/schema";

type Schemas = components["schemas"];

export type ChatSessionDto = Schemas["ChatSession"];
export type ChatMessageDto = Schemas["ChatMessage"];
export type ChatFinalizeRequestDto = Schemas["ChatFinalize"];
export type ChatFinalizeResponseDto = Schemas["ChatFinalizeResponse"];
export type ChatNonStreamResponseDto = Schemas["ChatNonStreamResponse"];
export type GuestChatRequestDto = Schemas["GuestChat"];
export type ChatCheckpointEventDto = Schemas["ChatCheckpointEvent"];
export type ChatDeltaEventDto = Schemas["ChatDeltaEvent"];
export type ChatDoneEventDto = Schemas["ChatDoneEvent"];
export type GuestChatDeltaEventDto = Schemas["GuestChatDeltaEvent"];
export type GuestChatDoneEventDto = Schemas["GuestChatDoneEvent"];
export type ChatErrorEventDto = Schemas["ChatErrorEvent"];
export type ChatProgressEventDto = Schemas["ChatProgressEvent"];
export type ChatTurnHistoryDto = Schemas["ChatTurn"];
export type ChatTurnPageDto = Schemas["PaginatedChatTurnList"];

export type MemberChatSseEvent =
  | { event: "checkpoint"; data: ChatCheckpointEventDto }
  | { event: "delta"; data: ChatDeltaEventDto }
  | { event: "progress"; data: ChatProgressEventDto }
  | { event: "done"; data: ChatDoneEventDto }
  | { event: "error"; data: ChatErrorEventDto };

export type GuestChatSseEvent =
  | { event: "delta"; data: GuestChatDeltaEventDto }
  | { event: "progress"; data: ChatProgressEventDto }
  | { event: "done"; data: GuestChatDoneEventDto }
  | { event: "error"; data: ChatErrorEventDto };

export type ChatCourseMetadataDto = Pick<ChatDoneEventDto, "places" | "coursePayload" | "route">;
