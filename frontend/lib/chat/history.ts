import type { ChatMessage, ChatProgressOperation } from "./types";
import type { ChatMessageDto, ChatTurnHistoryDto } from "./wire";
import { parseProgressEvent, reduceProgress } from "./progress";

type OrderedMessage = { order: number; message: ChatMessage };

export function commitChatLoad(signal: AbortSignal, isCurrent: () => boolean, commit: () => void): boolean {
  if (signal.aborted || !isCurrent()) return false;
  commit();
  return true;
}

export function restoreChatMessages(history: ChatMessageDto[], turns: ChatTurnHistoryDto[]): ChatMessage[] {
  const restored: OrderedMessage[] = history.map(item => ({
    order: item.sequence_no * 2,
    message: { id: item.id, role: item.role === "human" ? "user" : "assistant", content: item.content },
  }));

  for (const turn of turns) {
    let human = turn.human_message_id === null ? undefined : restored.find(item => item.message.id === turn.human_message_id);
    if (!human) {
      human = { order: (turn.base_sequence + 1) * 2, message: { role: "user", content: turn.question, ...(turn.human_message_id === null ? {} : { id: turn.human_message_id }) } };
      restored.push(human);
    }
    const progress = turn.progress.reduce<ChatProgressOperation[]>((operations, raw) => {
      const event = parseProgressEvent(raw, turn.id);
      return event ? reduceProgress(operations, event) : operations;
    }, []);
    const assistant = turn.assistant_message_id === null ? undefined : restored.find(item => item.message.id === turn.assistant_message_id);
    if (assistant) {
      assistant.message = { ...assistant.message, ...(progress.length ? { progress } : {}), turnStatus: turn.status };
    } else if (progress.length || turn.status !== "completed") {
      restored.push({ order: human.order + 1, message: { role: "assistant", content: "", progress, turnStatus: turn.status } });
    }
  }
  return restored.sort((left, right) => left.order - right.order).map(item => item.message);
}
