import type { ChatMessage, ChatToolCall } from "./types";
import { parsePlanning } from "./planning";
import { buildTimeline } from "./types";
import type { ChatAttachmentDto, ChatMessageDto, ChatToolCallDto } from "./wire";
import type { ChatAttachment } from "./types";

export const fromAttachmentDto = (item: ChatAttachmentDto): ChatAttachment => ({ id: item.id, kind: item.kind, name: item.name, contentType: item.content_type, size: item.size, width: item.width, height: item.height, url: item.url, createdAt: item.created_at });

export const fromToolDto = (tool: ChatToolCallDto): ChatToolCall => ({
  id: tool.id, toolName: tool.tool_name, status: tool.status, kind: tool.kind ?? "tool", parentId: tool.parent_id ?? null,
  ...(tool.title ? { title: tool.title } : {}), ...(tool.summary ? { summary: tool.summary } : {}), ...(tool.detail ? { detail: tool.detail } : {}),
});

export function commitChatLoad(signal: AbortSignal, isCurrent: () => boolean, commit: () => void): boolean {
  if (signal.aborted || !isCurrent()) return false;
  commit();
  return true;
}

// The server returns every stored item in canonical turn order (project_history); preserve that
// order as-is, do not re-sort by id. A turn with no answer (pending/failed/stopped before any
// reply) has only the user row, which then carries that turn's tools; once answered, tools move
// to the assistant row and the user row's tools are empty.
export function restoreChatMessages(history: ChatMessageDto[]): ChatMessage[] {
  return history.map(item => {
    const tools = item.tools.map(fromToolDto), timeline = buildTimeline(item.steps ?? [], tools), planning = parsePlanning(item.planning);
    return {
      id: item.id, role: item.role, content: item.content, status: item.status,
      ...(item.attachments ? { attachments: item.attachments.map(fromAttachmentDto) } : {}),
      ...(item.tool_group_ids ? { toolGroupIds: item.tool_group_ids } : {}),
      ...(item.answer_deleted ? { answerDeleted: true } : {}),
      ...(item.feedback !== undefined ? { feedback: item.feedback } : {}),
      ...(planning ? { planning } : {}),
      ...(tools.length ? { tools } : {}), ...(timeline.length ? { timeline } : {}),
    };
  });
}
