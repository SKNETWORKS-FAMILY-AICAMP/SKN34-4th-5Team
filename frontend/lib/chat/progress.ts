import { isRecord } from "./validation";
import type { ChatProgressEvent, ChatProgressOperation } from "./types";

export const MAX_PROGRESS_EVENT_BYTES = 20 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FIELDS = new Set(["turn_id", "sequence_no", "operation_id", "parent_operation_id", "kind", "status", "label", "created_at", "tool_name", "tool_call_id", "arguments", "result", "truncated", "summary"]);

export function parseProgressEvent(value: unknown, expectedTurnId?: string): ChatProgressEvent | null {
  if (!isRecord(value) || Object.keys(value).some(key => !FIELDS.has(key))) return null;
  let bytes: number;
  try { bytes = new TextEncoder().encode(JSON.stringify(value)).byteLength; } catch { return null; }
  if (bytes > MAX_PROGRESS_EVENT_BYTES || typeof value.turn_id !== "string" || !UUID.test(value.turn_id) || (expectedTurnId && value.turn_id !== expectedTurnId) ||
      !Number.isSafeInteger(value.sequence_no) || Number(value.sequence_no) < 1 || typeof value.operation_id !== "string" || !UUID.test(value.operation_id) ||
      (value.parent_operation_id !== null && (typeof value.parent_operation_id !== "string" || !UUID.test(value.parent_operation_id))) ||
      !["phase", "retrieval", "tool"].includes(String(value.kind)) || !["started", "completed", "failed", "interrupted", "unknown"].includes(String(value.status)) ||
      typeof value.label !== "string" || !value.label.trim() || value.label.length > 160 || typeof value.created_at !== "string" || value.created_at.length > 64 || !/^\d{4}-\d{2}-\d{2}T/.test(value.created_at) || !Number.isFinite(Date.parse(value.created_at)) ||
      (value.tool_name !== null && (typeof value.tool_name !== "string" || value.tool_name.length > 80)) ||
      ("tool_call_id" in value && value.tool_call_id !== null && (typeof value.tool_call_id !== "string" || value.tool_call_id.length > 255)) ||
      ("arguments" in value && value.arguments !== null && !isRecord(value.arguments)) ||
      ("result" in value && value.result !== null && !isRecord(value.result)) ||
      ("truncated" in value && typeof value.truncated !== "boolean") ||
      (value.summary !== null && !isRecord(value.summary))) return null;
  return {
    turnId: value.turn_id,
    sequenceNo: Number(value.sequence_no),
    operationId: value.operation_id,
    parentOperationId: value.parent_operation_id as string | null,
    kind: value.kind as ChatProgressEvent["kind"],
    status: value.status as ChatProgressEvent["status"],
    label: value.label,
    createdAt: value.created_at,
    toolName: value.tool_name as string | null,
    ...(typeof value.tool_call_id === "string" || value.tool_call_id === null ? { toolCallId: value.tool_call_id } : {}),
    ...(isRecord(value.arguments) || value.arguments === null ? { arguments: value.arguments } : {}),
    ...(isRecord(value.result) || value.result === null ? { result: value.result } : {}),
    ...(typeof value.truncated === "boolean" ? { truncated: value.truncated } : {}),
    summary: value.summary as Record<string, unknown> | null,
  };
}

export const isUuid = (value: unknown): value is string => typeof value === "string" && UUID.test(value);

export function reduceProgress(current: ChatProgressOperation[], event: ChatProgressEvent): ChatProgressOperation[] {
  const index = current.findIndex(operation => operation.operationId === event.operationId);
  const previous = index >= 0 ? current[index] : undefined;
  const next: ChatProgressOperation = {
    ...event,
    toolName: event.toolName ?? previous?.toolName ?? null,
    toolCallId: event.toolCallId ?? previous?.toolCallId,
    arguments: event.arguments ?? previous?.arguments,
    result: event.result ?? previous?.result,
    truncated: event.truncated || previous?.truncated,
    ...(previous?.startedAt ? { startedAt: previous.startedAt, startSequenceNo: previous.startSequenceNo } : {}),
    ...(event.status === "started" ? { startedAt: event.createdAt, startSequenceNo: event.sequenceNo } : {}),
  };
  if (index < 0) return [...current, next];
  return current.map((operation, operationIndex) => operationIndex === index ? next : operation);
}

export const settleProgress = (current: ChatProgressOperation[]): ChatProgressOperation[] =>
  current.map(operation => operation.status === "started" ? { ...operation, status: "unknown" } : operation);
