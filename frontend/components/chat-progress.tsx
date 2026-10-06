import type { ChatProgressOperation, ChatProgressStatus } from "@/lib/chat/types";
import "@/styles/chat-progress.css";

const STATUS: Record<ChatProgressStatus, { mark: string; label: string }> = {
  started: { mark: "…", label: "진행 중" },
  completed: { mark: "✓", label: "완료" },
  failed: { mark: "!", label: "실패" },
  interrupted: { mark: "–", label: "중단" },
  unknown: { mark: "?", label: "결과 확인 불가" },
};
const GENERIC_LABELS = new Set(["조회 중", "조회 완료", "조회 실패", "조회 중단"]);

function displayLabel(operation: ChatProgressOperation) {
  if (!GENERIC_LABELS.has(operation.label) || !operation.toolName) return operation.label;
  const status = operation.status === "started" ? "호출 중" : operation.status === "completed" ? "호출 완료" : STATUS[operation.status].label;
  return `${operation.toolName} ${status}`;
}

export function ChatProgress({ operations }: { operations: ChatProgressOperation[] }) {
  const tools = operations.filter(operation => operation.kind === "tool");
  if (!tools.length) return null;
  return (
    <ul className="chat-progress" role="status" aria-label="도구 호출 로그" aria-live="polite" aria-atomic="false">
        {tools.map(operation => {
          const status = STATUS[operation.status];
          const details = operation.toolCallId || operation.arguments || operation.result;
          return <li key={operation.operationId} className={`is-${operation.status}`}><span aria-hidden="true">{status.mark}</span><div><span>{displayLabel(operation)}</span>{details && <details><summary>관리자 로그</summary><code>{operation.toolName ?? "tool"}</code>{operation.toolCallId && <code>{operation.toolCallId}</code>}{operation.arguments && <><strong>입력</strong><pre>{JSON.stringify(operation.arguments, null, 2)}</pre></>}{operation.result && <><strong>결과</strong><pre>{JSON.stringify(operation.result, null, 2)}</pre></>}{operation.truncated && <small>일부 안전한 정보만 표시했어요.</small>}</details>}</div></li>;
        })}
    </ul>
  );
}
