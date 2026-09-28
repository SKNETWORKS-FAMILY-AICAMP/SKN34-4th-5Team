import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-direct-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
for (const name of ["lib/member-auth-request", "lib/chat/types", "lib/chat/validation", "lib/chat/course", "lib/chat/history", "lib/chat/client"]) {
  const source = readFileSync(join(frontend, `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}

global.window = { setTimeout, clearTimeout };
const stored = new Map();
global.sessionStorage = {
  getItem: key => stored.get(key) ?? null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
};
const require = createRequire(join(scratch, "entry.cjs"));
const { clearMemberTokens, saveMemberTokens } = require("./lib/member-auth-request.js");
const { ChatClientError, deleteChatMessages, deleteChatSession, editChatMessage, fetchChatHistory, getChatStatus, listChatSessions, renameChatSession, sendChatMessage } = require("./lib/chat/client.js");
const { restoreChatMessages } = require("./lib/chat/history.js");
const { courseToStops, parseChatCourse } = require("./lib/chat/course.js");
const json = (value, status = 200) => Response.json(value, { status });
const sse = events => new Response(new ReadableStream({
  start(controller) {
    controller.enqueue(new TextEncoder().encode(events.map(([event, data]) =>
      `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("")));
    controller.close();
  },
}), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
// Session ids are UUIDs, message ids are integers; done.message_id is the saved assistant id as a digit string.
const SESSION = "3f2c1a4e-8b7d-4c21-9e0f-5a6b7c8d9e01";
const OTHER_SESSION = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const room = (id = SESSION, title = "첫 질문") => ({ id, title, created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z" });
const row = (id, sequence_no, role, content, status = "completed") => ({ id, sequence_no, role, content, status, tools: [], created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z" });
const answerEvents = (chunks = ["첫 ", "답변"], messageId = "12") => [
  ...chunks.map(text => ["delta", { text }]),
  ["done", { message_id: messageId, assistant_message: chunks.join("") }],
];
const record = calls => async (url, init = {}) => {
  const call = { url: String(url), method: init.method ?? "GET", body: init.body ? JSON.parse(init.body) : undefined, headers: new Headers(init.headers), credentials: init.credentials };
  calls.push(call);
  return call;
};
const hanging = init => new Response(new ReadableStream({
  start(stream) {
    stream.enqueue(new TextEncoder().encode('event: delta\ndata: {"text": "부분 답"}\n\n'));
    init.signal.addEventListener("abort", () => stream.error(new DOMException("Aborted", "AbortError")), { once: true });
  },
}), { headers: { "Content-Type": "text/event-stream" } });

beforeEach(() => { stored.clear(); clearMemberTokens(); });

test("member send creates a UUID session then streams v2 delta/done with Bearer auth", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const calls = [], log = record(calls);
  global.fetch = async (url, init = {}) => {
    const call = await log(url, init);
    if (call.method === "GET") return json([]);
    if (call.url === "/api/v2/chat/sessions/") return json(room(), 201);
    return sse(answerEvents());
  };
  assert.equal((await getChatStatus("member")).provider, "backend");
  const seen = [];
  const reply = await sendChatMessage("member", { content: "  첫 질문 ", context: { stadium: "잠실야구장", intent: "route", origin: { lat: 37.5, lng: 127.07 } } }, undefined, { onDelta: value => seen.push(value) });
  assert.deepEqual(seen, ["첫 ", "첫 답변"]);
  assert.deepEqual({ reply: reply.reply, sessionId: reply.sessionId, assistant: reply.assistantMessageId, provider: reply.provider },
    { reply: "첫 답변", sessionId: SESSION, assistant: 12, provider: "backend" });
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ["GET", "/api/v2/chat/sessions/"], ["POST", "/api/v2/chat/sessions/"], ["POST", `/api/v2/chat/sessions/${SESSION}/messages/`],
  ]);
  assert.deepEqual(calls[1].body, { title: "첫 질문" });
  // Only the new question is sent: the server owns history. Context goes as the structured object v2 reads.
  assert.deepEqual(calls[2].body, { content: "첫 질문", context: { stadium: "잠실야구장", intent: "route", origin: { lat: 37.5, lng: 127.07 } } });
  assert.equal(calls[2].headers.get("Accept"), "text/event-stream");
  assert.ok(calls.every(call => call.headers.get("Authorization") === "Bearer access-token"));
});

test("guest session is cookie-owned: no Authorization, no credentials override, and reload lists the same UUID", async () => {
  const calls = [], log = record(calls);
  let cookieIssued = false;
  global.fetch = async (url, init = {}) => {
    const call = await log(url, init);
    if (call.url === "/api/v2/chat/sessions/" && call.method === "POST") { cookieIssued = true; return json(room(SESSION, "비회원 질문"), 201); }
    if (call.url === "/api/v2/chat/sessions/") return json(cookieIssued ? [room(SESSION, "비회원 질문")] : []);
    if (call.method === "GET") return json([row(1, 1, "user", "비회원 질문"), row(2, 2, "assistant", "비회원 답")]);
    return sse(answerEvents(["비회원 ", "답"], "2"));
  };
  assert.deepEqual(await listChatSessions("guest"), []);
  const reply = await sendChatMessage("guest", { content: "비회원 질문" });
  assert.equal(reply.provider, "guest");
  assert.equal(reply.sessionId, SESSION);
  // Reload: the browser resends the HttpOnly guest_id cookie, so the list and history come back.
  const sessions = await listChatSessions("guest");
  assert.deepEqual(sessions.map(item => item.id), [SESSION]);
  const restored = restoreChatMessages(await fetchChatHistory("guest", sessions[0].id));
  assert.deepEqual(restored, [
    { id: 1, role: "user", content: "비회원 질문", status: "completed" },
    { id: 2, role: "assistant", content: "비회원 답", status: "completed" },
  ]);
  assert.ok(calls.every(call => call.headers.get("Authorization") === null && call.credentials === undefined));
  assert.ok(calls.every(call => call.url.startsWith("/api/v2/chat/sessions/")));
});

test("edit PUTs message_id and content, then streams the regenerated answer", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const calls = [], log = record(calls);
  global.fetch = async (url, init = {}) => { await log(url, init); return sse(answerEvents(["고친 ", "답"], "31")); };
  const reply = await editChatMessage("member", { sessionId: SESSION, messageId: 21, content: "고친 질문", context: { intent: "baseball" } });
  assert.deepEqual([calls[0].method, calls[0].url], ["PUT", `/api/v2/chat/sessions/${SESSION}/messages/`]);
  assert.deepEqual(calls[0].body, { message_id: 21, content: "고친 질문", context: { intent: "baseball" } });
  assert.deepEqual({ reply: reply.reply, id: reply.assistantMessageId }, { reply: "고친 답", id: 31 });
  await assert.rejects(editChatMessage("member", { sessionId: SESSION, messageId: 0, content: "x" }), error => error instanceof ChatClientError && error.status === 400);
});

test("delete truncates with a DELETE body and accepts the empty 204", async () => {
  const calls = [], log = record(calls);
  global.fetch = async (url, init = {}) => {
    const call = await log(url, init);
    if (call.body?.message_id === 99) return json({ detail: "No ChatMessage matches the given query." }, 404);
    return new Response(null, { status: 204 });
  };
  assert.equal(await deleteChatMessages("guest", SESSION, 21), undefined);
  assert.deepEqual([calls[0].method, calls[0].url, calls[0].body], ["DELETE", `/api/v2/chat/sessions/${SESSION}/messages/`, { message_id: 21 }]);
  assert.equal(calls[0].headers.get("Content-Type"), "application/json");
  await assert.rejects(deleteChatMessages("guest", SESSION, 99), error => error instanceof ChatClientError && error.status === 404 && !error.uncertain);
});

test("session rename/delete/history use UUID paths and validate the current DTOs", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const calls = [], log = record(calls);
  global.fetch = async (url, init = {}) => {
    const call = await log(url, init);
    if (call.method === "DELETE") return new Response(null, { status: 204 });
    if (call.method === "PATCH") return json(room(SESSION, call.body.title));
    return json([row(5, 2, "assistant", "답"), row(4, 1, "user", "질문", "failed")]);
  };
  assert.equal((await renameChatSession("member", SESSION, "이름")).title, "이름");
  const history = await fetchChatHistory("member", SESSION);
  assert.deepEqual(restoreChatMessages(history).map(item => [item.id, item.role, item.status]), [[4, "user", "failed"], [5, "assistant", "completed"]]);
  assert.equal(await deleteChatSession("member", SESSION), undefined);
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ["PATCH", `/api/v2/chat/sessions/${SESSION}/`], ["GET", `/api/v2/chat/sessions/${SESSION}/messages/`], ["DELETE", `/api/v2/chat/sessions/${SESSION}/`],
  ]);
  assert.ok(calls.every(call => call.headers.get("Authorization") === "Bearer access-token"));
  await assert.rejects(fetchChatHistory("member", "7"), error => error instanceof ChatClientError && error.status === 400);
  global.fetch = async () => json([{ id: 7, title: "숫자 id 는 옛 계약" }]);
  await assert.rejects(listChatSessions("member"), error => error instanceof ChatClientError && error.status === 502);
  global.fetch = async () => json([{ ...row(1, 1, "human", "옛 역할") }]);
  await assert.rejects(fetchChatHistory("member", SESSION), error => error instanceof ChatClientError && error.status === 502);
});

test("stream error frame is a known failure, not an uncertain delivery", async () => {
  global.fetch = async () => sse([["delta", { text: "부분" }], ["error", { detail: "답변 생성에 실패했습니다. 다시 시도해 주세요." }]]);
  await assert.rejects(sendChatMessage("guest", { sessionId: SESSION, content: "질문" }), error =>
    error instanceof ChatClientError && error.message === "답변 생성에 실패했습니다. 다시 시도해 주세요." && !error.uncertain && error.sessionId === SESSION);
});

test("stream that closes without done or error (superseded by edit/delete) is reported, never shown as saved", async () => {
  global.fetch = async () => sse([["delta", { text: "지워진 턴" }]]);
  await assert.rejects(sendChatMessage("guest", { sessionId: SESSION, content: "질문" }), error =>
    error instanceof ChatClientError && error.uncertain && /연결이 끊겼/.test(error.message));
  global.fetch = async () => sse([["delta", { text: "답" }], ["done", { message_id: 12, assistant_message: "답" }]]);
  await assert.rejects(sendChatMessage("guest", { sessionId: SESSION, content: "질문" }), error => error instanceof ChatClientError && /최종 답변/.test(error.message));
  global.fetch = async () => sse([["checkpoint", { turn_id: "old", receipt: "x" }]]);
  await assert.rejects(sendChatMessage("guest", { sessionId: SESSION, content: "질문" }), error => error instanceof ChatClientError && /알 수 없는/.test(error.message));
});

test("SSE reader handles split frames and UTF-8 boundaries", async () => {
  const bytes = new TextEncoder().encode(answerEvents(["잠실 ", "야구장"], "44").map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""));
  global.fetch = async () => new Response(new ReadableStream({
    start(stream) { for (let index = 0; index < bytes.length; index += 5) stream.enqueue(bytes.slice(index, index + 5)); stream.close(); },
  }), { headers: { "Content-Type": "text/event-stream" } });
  const reply = await sendChatMessage("guest", { sessionId: SESSION, content: "질문" });
  assert.deepEqual([reply.reply, reply.assistantMessageId], ["잠실 야구장", 44]);
});

test("pre-stream JSON errors surface the DRF detail", async () => {
  global.fetch = async () => json({ content: ["이 필드는 2200자 이하여야 합니다."] }, 400);
  await assert.rejects(sendChatMessage("guest", { sessionId: SESSION, content: "질문" }), error => error instanceof ChatClientError && error.status === 400 && /2200/.test(error.message));
  global.fetch = async () => json({ detail: "session not found" }, 404);
  await assert.rejects(sendChatMessage("guest", { sessionId: OTHER_SESSION, content: "질문" }), error => error instanceof ChatClientError && error.status === 404);
});

test("Stop is a local abort: it rejects with 499 and returns no saved partial answer", async () => {
  const controller = new AbortController();
  let received = "";
  global.fetch = async (url, init = {}) => hanging(init);
  await assert.rejects(
    sendChatMessage("guest", { sessionId: SESSION, content: "질문" }, controller.signal, { onDelta: answer => { received = answer; controller.abort(); } }),
    error => error instanceof ChatClientError && error.status === 499,
  );
  assert.equal(received, "부분 답");
});

const coursePlaces = [
  { phase: "BEFORE", name: "상무초밥 잠실점", lat: 37.51, lng: 127.08, category: "FOOD", placeId: "123456", address: "서울 송파구", reason: "초밥", time: "16:06", stayMin: 50 },
  { phase: "BEFORE", name: "좌표 없는 곳", lat: null, lng: 127.08, category: "CAFE" },
  { phase: "GAME", name: "잠실야구장", lat: 37.512, lng: 127.072, category: "STADIUM", placeId: null, time: "17:45" },
  { phase: "AFTER", name: "잠실 게스트하우스", lat: 37.51, lng: 127.08, category: "STAY", placeId: "555", time: "22:49" },
];
test("course parsing ignores non-course answers and builds editable stops", () => {
  assert.equal(parseChatCourse({ assistant_message: "LG는 3위예요" }), undefined);
  assert.equal(parseChatCourse({ places: [{ phase: "GAME", name: "잠실야구장", lat: 37.5, lng: 127, category: "STADIUM" }] }), undefined);
  const course = parseChatCourse({ places: coursePlaces, stadiumCode: "JAMSIL", travel: { mode: "walk" } });
  assert.equal(course.travelMode, "walk");
  let id = 0;
  const stops = courseToStops(course, () => `id-${++id}`);
  assert.deepEqual(stops.map(stop => [stop.name, stop.category, stop.placeId, stop.isDrawnPoint]), [
    ["상무초밥 잠실점", "먹거리", "123456", true],
    ["잠실야구장", "야구장", "chat:stadium:JAMSIL", true],
    ["잠실 게스트하우스", "숙박", "555", true],
  ]);
  assert.ok(stops.every(stop => stop.visitId));
});

test("member auth failure never falls back to the guest cookie", async () => {
  global.fetch = async () => { throw new Error("no request may be sent without a member token"); };
  await assert.rejects(getChatStatus("member"), error => error instanceof ChatClientError && error.status === 401);
  await assert.rejects(sendChatMessage("member", { sessionId: SESSION, content: "질문" }), error => error instanceof ChatClientError && error.status === 401);
});

test("provider enables guest chat, keeps stop local and clears state on every identity switch", () => {
  const provider = readFileSync(join(frontend, "components/chat-provider.tsx"), "utf8");
  const surfaces = ["components/chat-popup.tsx", "components/chat-workspace.tsx"].map(path => readFileSync(join(frontend, path), "utf8"));
  assert.match(provider, /const identity = memberStatus === "authenticated"/);
  assert.match(provider, /memberStatus === "authenticated" \? "member" : memberStatus === "anonymous" \? "guest" : null/);
  for (const cleanup of ["controller.abort()", "backendSessions.current.clear()", "archivedConversations.current.clear()", "historyRef.current = []", "syncRequestRef.current?.abort()"]) assert.ok(provider.includes(cleanup));
  assert.match(provider, /받던 답변은 저장되지 않아요/);
  assert.match(provider, /이 질문 이후의 대화는 모두 지워지고/);
  assert.match(provider, /이 질문과 이후 대화를 모두 지울까요/);
  assert.doesNotMatch(provider, /localStorage|sessionStorage|document\.cookie|credentials/);
  assert.doesNotMatch(provider, /checkpoint|finalize|fetchChatTurns|sendGuestChatMessage|받은 답변까지/);
  assert.match(provider, /messages: identityChanged \? \[\] : messages/);
  for (const surface of surfaces) {
    assert.match(surface, /답변 생성 중단/);
    assert.match(surface, /받던 답변은 저장되지 않아요/);
    assert.match(surface, /aria-relevant="additions"/);
    assert.match(surface, /message\.id !== undefined && available && !busy/);
    assert.doesNotMatch(surface, /로그인하고 질문하기|조회만 할 수 있어요|받은 답변까지 보관/);
  }
});

test("authenticated chat has no legacy Next cookie relay", () => {
  assert.equal(existsSync(join(frontend, "app/chat-api/route.ts")), false);
  assert.equal(existsSync(join(frontend, "lib/chat/team.ts")), false);
  assert.equal(existsSync(join(frontend, "app/baseball-admin-api/route.ts")), false);
});

test("client has no retired v1 guest, turns, finalize or non-stream endpoints", () => {
  const client = readFileSync(join(frontend, "lib/chat/client.ts"), "utf8");
  assert.doesNotMatch(client, /\/api\/v1\/|guest\/|turns|finalize|checkpoint|contextPrefix|credentials|document\.cookie/);
});
