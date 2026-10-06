import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-progress-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
symlinkSync(join(frontend, "node_modules"), join(scratch, "node_modules"), "dir");
for (const name of ["lib/member-auth-request", "lib/chat/types", "lib/chat/validation", "lib/chat/course", "lib/chat/progress", "lib/chat/history", "lib/chat/client"]) {
  const source = readFileSync(join(frontend, `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
{
  const source = readFileSync(join(frontend, "components/chat-pending.tsx"), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: "chat-pending.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  mkdirSync(join(scratch, "components"), { recursive: true });
  writeFileSync(join(scratch, "components/chat-pending.js"), outputText);
}
{
  const source = readFileSync(join(frontend, "components/chat-progress.tsx"), "utf8").replace(/^import "@\/styles\/chat-progress\.css";$/m, "");
  const { outputText } = ts.transpileModule(source, {
    fileName: "chat-progress.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  writeFileSync(join(scratch, "components/chat-progress.js"), outputText);
}

global.window = { setTimeout, clearTimeout, location: { origin: "http://localhost" } };
const stored = new Map();
global.sessionStorage = {
  getItem: key => stored.get(key) ?? null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
};
const require = createRequire(join(scratch, "entry.cjs"));
const { clearMemberTokens, saveMemberTokens } = require("./lib/member-auth-request.js");
const { parseChatRequest } = require("./lib/chat/validation.js");
const { MAX_PROGRESS_EVENT_BYTES, parseProgressEvent, reduceProgress, settleProgress } = require("./lib/chat/progress.js");
const { commitChatLoad, restoreChatMessages } = require("./lib/chat/history.js");
const { ChatClientError, fetchChatTurns, sendChatMessage, sendGuestChatMessage } = require("./lib/chat/client.js");
const { ChatPending } = require("./components/chat-pending.js");
const { ChatProgress } = require("./components/chat-progress.js");

const TURN = "11111111-1111-4111-8111-111111111111";
const OTHER_TURN = "22222222-2222-4222-8222-222222222222";
const PARENT = "33333333-3333-4333-8333-333333333333";
const CHILD = "44444444-4444-4444-8444-444444444444";
const event = (overrides = {}) => ({
  turn_id: TURN,
  sequence_no: 1,
  operation_id: PARENT,
  parent_operation_id: null,
  kind: "phase",
  status: "started",
  label: "응답을 준비하고 있어요",
  created_at: "2026-09-16T03:00:00Z",
  tool_name: null,
  summary: null,
  ...overrides,
});
const frame = (name, data) => `event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
const stream = (text, splitUtf8 = false) => new Response(new ReadableStream({
  start(controller) {
    const bytes = new TextEncoder().encode(text);
    if (!splitUtf8) controller.enqueue(bytes);
    else {
      const korean = bytes.findIndex(byte => byte >= 0xe0);
      for (const chunk of [bytes.slice(0, 7), bytes.slice(7, korean + 1), bytes.slice(korean + 1, korean + 2), bytes.slice(korean + 2)]) if (chunk.length) controller.enqueue(chunk);
    }
    controller.close();
  },
}), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
const json = value => Response.json(value);

beforeEach(() => { stored.clear(); clearMemberTokens(); });

test("pre-answer busy indicator renders until the first streamed text", () => {
  for (const className of ["workspace-thinking", "chat-popup-thinking"]) {
    const pending = renderToStaticMarkup(React.createElement(ChatPending, { busy: true, streaming: "", className }));
    assert.match(pending, /응답 준비 중…/);
    assert.equal((pending.match(/<i><\/i>/g) ?? []).length, 3);
  }
  assert.equal(renderToStaticMarkup(React.createElement(ChatPending, { busy: true, streaming: "첫 토큰", className: "workspace-thinking" })), "");
  assert.equal(renderToStaticMarkup(React.createElement(ChatPending, { busy: false, streaming: "", className: "workspace-thinking" })), "");
});

test("tool log renders Korean server labels and keeps raw names admin-only", () => {
  const base = {
    turnId: TURN, sequenceNo: 2, operationId: CHILD, parentOperationId: null,
    kind: "tool", status: "completed", label: "경기 일정 조회 완료",
    createdAt: "2026-09-16T03:00:00Z", toolName: "get_games", summary: null,
  };
  const ordinary = renderToStaticMarkup(React.createElement(ChatProgress, { operations: [base] }));
  assert.match(ordinary, /경기 일정 조회 완료/);
  assert.doesNotMatch(ordinary, /get_games|완료 완료|진행 과정|단계/);

  const admin = renderToStaticMarkup(React.createElement(ChatProgress, { operations: [{ ...base, toolCallId: "call-1", arguments: { team: "LG" }, result: { count: 1 } }] }));
  assert.match(admin, /경기 일정 조회 완료/);
  assert.match(admin, /관리자 로그/);
  assert.match(admin, /get_games/);

  const unknown = renderToStaticMarkup(React.createElement(ChatProgress, { operations: [{ ...base, status: "started", label: "조회 중", toolName: "new_tool" }] }));
  assert.match(unknown, /new_tool 호출 중/);
});

test("progress parser validates the frozen public contract and byte limit", () => {
  const parsed = parseProgressEvent(event({ summary: { count: 2 } }), TURN);
  assert.equal(parsed.label, "응답을 준비하고 있어요");
  assert.deepEqual(parsed.summary, { count: 2 });
  assert.equal(parseProgressEvent(event({ turn_id: "not-a-uuid" })), null);
  assert.equal(parseProgressEvent(event({ label: "x".repeat(161) })), null);
  assert.equal(parseProgressEvent(event({ summary: [] })), null);
  assert.equal(parseProgressEvent(event({ summary: { value: "가".repeat(MAX_PROGRESS_EVENT_BYTES) } })), null);
  assert.equal(parseProgressEvent({ ...event(), raw_prompt: "비공개" }), null);
});

test("display progress never enters the model message payload", () => {
  const parsed = parseChatRequest({ messages: [{ role: "assistant", content: "이전 답변", progress: [{ secret: "no" }] }, { role: "user", content: "후속 질문", progress: [event()] }] });
  assert.deepEqual(parsed.messages, [{ role: "assistant", content: "이전 답변" }, { role: "user", content: "후속 질문" }]);
});

test("display-only empty assistant placeholders do not block a follow-up", async () => {
  const restored = restoreChatMessages([], [{ id: TURN, question: "멈춘 질문", status: "stopped", base_sequence: 0, human_message_id: null, assistant_message_id: null, progress: [event()] }]);
  assert.deepEqual(restored.map(message => [message.role, message.content]), [["user", "멈춘 질문"], ["assistant", ""]]);
  assert.throws(() => parseChatRequest({ messages: [...restored, { role: "user", content: "후속 질문" }] }), /비어 있거나/);
  let posted;
  global.fetch = async (_url, init) => {
    posted = JSON.parse(init.body);
    return stream(frame("delta", { text: "후속 답변" }) + frame("done", { assistant_message: "후속 답변" }));
  };
  await sendGuestChatMessage({ messages: [...restored, { role: "user", content: "후속 질문" }] });
  assert.deepEqual(posted.messages, [
    { role: "user", content: "멈춘 질문" },
    { role: "user", content: "후속 질문" },
  ]);
  await sendGuestChatMessage({ messages: [
    { role: "user", content: "첫 토큰 전에 멈춘 질문" },
    { role: "assistant", content: "", turnStatus: "stopped" },
    { role: "user", content: "다시 질문" },
  ] });
  assert.deepEqual(posted.messages, [
    { role: "user", content: "첫 토큰 전에 멈춘 질문" },
    { role: "user", content: "다시 질문" },
  ]);
  assert.throws(() => parseChatRequest({ messages: [{ role: "assistant", content: "" }, { role: "user", content: "질문" }] }), /비어 있거나/);
  assert.throws(() => parseChatRequest({ messages: [{ role: "user", content: "" }] }), /비어 있거나/);
});

test("late chat loads cannot commit after a local action invalidates them", async () => {
  const controller = new AbortController();
  const state = { messages: ["새 질문", "새 답변"], draft: "작성 중", progress: ["조회 완료"], sessions: ["새 대화"] };
  let resolve;
  const late = new Promise(done => { resolve = done; }).then(snapshot => {
    commitChatLoad(controller.signal, () => true, () => Object.assign(state, snapshot));
  });
  controller.abort();
  resolve({ messages: ["옛 기록"], draft: "", progress: [], sessions: ["옛 대화"] });
  await late;
  assert.deepEqual(state, { messages: ["새 질문", "새 답변"], draft: "작성 중", progress: ["조회 완료"], sessions: ["새 대화"] });

  const stillConnected = new AbortController();
  commitChatLoad(stillConnected.signal, () => false, () => Object.assign(state, { sessions: ["늦은 목록"] }));
  assert.deepEqual(state.sessions, ["새 대화"]);
});

test("operation reducer joins terminal events and preserves nested parents", () => {
  let operations = reduceProgress([], parseProgressEvent(event()));
  operations = reduceProgress(operations, parseProgressEvent(event({ sequence_no: 2, operation_id: CHILD, parent_operation_id: PARENT, kind: "tool", label: "경기 일정을 조회하고 있어요" })));
  operations = reduceProgress(operations, parseProgressEvent(event({ sequence_no: 3, operation_id: CHILD, parent_operation_id: PARENT, kind: "tool", status: "completed", label: "경기 일정 조회를 마쳤어요" })));
  assert.equal(operations.length, 2);
  assert.equal(operations[1].status, "completed");
  assert.equal(operations[1].parentOperationId, PARENT);
  assert.equal(operations[1].startedAt, "2026-09-16T03:00:00Z");
  assert.deepEqual(settleProgress(operations).map(operation => operation.status), ["unknown", "completed"]);
});

test("tool operations keep the real tool name through completion", () => {
  let operations = reduceProgress([], parseProgressEvent(event({
    kind: "tool", tool_name: "get_games",
  })));
  operations = reduceProgress(operations, parseProgressEvent(event({
    sequence_no: 2, kind: "tool", status: "completed", tool_name: "get_games",
  })));
  assert.equal(operations[0].toolName, "get_games");
  assert.equal(operations[0].status, "completed");
});

test("admin tool details are validated and merged without changing the ordinary contract", () => {
  let operations = reduceProgress([], parseProgressEvent(event({
    kind: "tool", tool_name: "get_games", tool_call_id: "call-1",
    arguments: { team: "LG" }, truncated: false,
  })));
  operations = reduceProgress(operations, parseProgressEvent(event({
    sequence_no: 2, kind: "tool", status: "completed", tool_name: "get_games",
    tool_call_id: "call-1", arguments: null, result: { count: 1 }, truncated: false,
  })));
  assert.equal(operations[0].toolCallId, "call-1");
  assert.deepEqual(operations[0].arguments, { team: "LG" });
  assert.deepEqual(operations[0].result, { count: 1 });
});

test("member SSE handles split frames, UTF-8 boundaries and identical duplicates", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const progress = event(), seen = [];
  global.fetch = async (url, init = {}) => {
    if (String(url).includes("/finalize/")) return json({ turn_id: TURN, session_id: 7, status: "completed", user_message_id: 1, assistant_message_id: 2, assistant_message: JSON.parse(init.body).prefix });
    return stream(frame("checkpoint", { turn_id: TURN, receipt: "empty" }) + frame("progress", progress) + frame("progress", progress) + frame("delta", { turn_id: TURN, receipt: "part", text: "완료" }) + frame("done", { turn_id: TURN, receipt: "done" }), true);
  };
  const reply = await sendChatMessage({ sessionId: 7, messages: [{ role: "user", content: "질문" }] }, undefined, { onProgress: value => seen.push(value), onDelta: (answer, checkpoint) => assert.equal(checkpoint.prefix, answer) });
  assert.equal(reply.reply, "완료");
  assert.equal(seen.length, 1);
  assert.equal(seen[0].turnId, TURN);
});

test("guest SSE emits progress without adding it to the answer", async () => {
  const seen = [];
  global.fetch = async () => stream(frame("progress", event()) + frame("delta", { text: "답변" }) + frame("done", { assistant_message: "답변" }), true);
  const reply = await sendGuestChatMessage({ messages: [{ role: "user", content: "질문" }] }, undefined, { onProgress: value => seen.push(value) });
  assert.equal(reply.reply, "답변");
  assert.equal(seen.length, 1);
});

for (const [name, frames] of [
  ["mismatched turn ids", frame("checkpoint", { turn_id: TURN, receipt: "empty" }) + frame("progress", event({ turn_id: OTHER_TURN }))],
  ["conflicting duplicate sequences", frame("checkpoint", { turn_id: TURN, receipt: "empty" }) + frame("progress", event()) + frame("progress", event({ label: "다른 상태" }))],
  ["oversize payloads", frame("checkpoint", { turn_id: TURN, receipt: "empty" }) + frame("progress", event({ summary: { value: "x".repeat(MAX_PROGRESS_EVENT_BYTES) } }))],
  ["unknown event types", frame("checkpoint", { turn_id: TURN, receipt: "empty" }) + frame("mystery", {})],
]) {
  test(`member SSE rejects ${name}`, async () => {
    saveMemberTokens("access-token", "refresh-token");
    global.fetch = async () => stream(frames);
    await assert.rejects(sendChatMessage({ sessionId: 7, messages: [{ role: "user", content: "질문" }] }), error => error instanceof ChatClientError && error.status === 502);
  });
}

test("turn history follows only same-origin pages on the same session endpoint", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const calls = [];
  global.fetch = async url => {
    calls.push(String(url));
    const second = String(url).endsWith("?page=2");
    const indexes = second ? [20] : Array.from({ length: 20 }, (_, index) => index);
    return json({ count: 21, next: second ? null : "/api/chat/sessions/7/turns/?page=2", previous: second ? "/api/chat/sessions/7/turns/?page=1" : null, results: indexes.map(index => ({ id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`, question: `질문 ${index + 1}`, status: "completed", base_sequence: index * 2, human_message_id: null, assistant_message_id: null, progress: [] })) });
  };
  const turns = await fetchChatTurns(7);
  assert.equal(turns.length, 21);
  assert.equal(restoreChatMessages([], turns).at(-1).content, "질문 21");
  assert.deepEqual(calls, ["/api/chat/sessions/7/turns/?page=1", "/api/chat/sessions/7/turns/?page=2"]);

  global.fetch = async () => json({ count: 1, next: "https://evil.example/api/chat/sessions/7/turns/?page=2", previous: null, results: [{ id: TURN, question: "질문", status: "failed", base_sequence: 0, human_message_id: null, assistant_message_id: null, progress: [] }] });
  await assert.rejects(fetchChatTurns(7), error => error instanceof ChatClientError && /다음 페이지/.test(error.message));
});

test("history restores unfinished questions with progress and leaves old turns unchanged", () => {
  const progress = [event(), event({ sequence_no: 2, status: "failed", label: "조회하지 못했어요" })];
  const restored = restoreChatMessages([], [{ id: TURN, question: "진행 중 질문", status: "failed", base_sequence: 0, human_message_id: null, assistant_message_id: null, progress }]);
  assert.deepEqual(restored.map(message => [message.role, message.content]), [["user", "진행 중 질문"], ["assistant", ""]]);
  assert.equal(restored[1].progress[0].status, "failed");

  const legacy = restoreChatMessages([{ id: 10, sequence_no: 1, role: "human", content: "옛 질문", status: "completed" }, { id: 11, sequence_no: 2, role: "ai", content: "옛 답변", status: "completed" }], [{ id: OTHER_TURN, question: "옛 질문", status: "completed", base_sequence: 0, human_message_id: 10, assistant_message_id: 11, progress: [] }]);
  assert.deepEqual(legacy.map(({ role, content }) => ({ role, content })), [{ role: "user", content: "옛 질문" }, { role: "assistant", content: "옛 답변" }]);
  assert.equal("progress" in legacy[1], false);
});

test("both chat surfaces share a tool-only call log without debug details", () => {
  const component = readFileSync(join(frontend, "components/chat-progress.tsx"), "utf8");
  const styles = readFileSync(join(frontend, "styles/chat-progress.css"), "utf8");
  for (const path of ["components/chat-popup.tsx", "components/chat-workspace.tsx"]) {
    const surface = readFileSync(join(frontend, path), "utf8");
    assert.match(surface, /<ChatProgress[^>]*operations=/);
    assert.match(surface, /<ChatPending busy=\{busy\} streaming=\{chat\.streaming\}/);
    assert.doesNotMatch(surface, /busy && !chat\.progress\.length/);
  }
  assert.match(component, /operation\.kind === "tool"/);
  assert.match(component, /operation\.toolName/);
  assert.match(component, /도구 호출 로그/);
  assert.match(component, /관리자 로그/);
  assert.doesNotMatch(component, /진행 과정|단계/);
  assert.doesNotMatch(component, /dangerouslySetInnerHTML/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  assert.match(styles, /overflow-wrap: anywhere/);
});
