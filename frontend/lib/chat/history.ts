import type { ChatMessage } from "./types";
import type { ChatMessageDto } from "./wire";

export function commitChatLoad(signal: AbortSignal, isCurrent: () => boolean, commit: () => void): boolean {
  if (signal.aborted || !isCurrent()) return false;
  commit();
  return true;
}

// The server returns every stored row in sequence_no order, including pending questions from an
// interrupted stream and failed assistant rows that hold only a partial answer.
export function restoreChatMessages(history: ChatMessageDto[]): ChatMessage[] {
  return [...history]
    .sort((left, right) => left.sequence_no - right.sequence_no)
    .map(item => ({ id: item.id, role: item.role, content: item.content, status: item.status }));
}
