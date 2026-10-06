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
for (const name of ["lib/member-auth-request", "lib/chat/types", "lib/chat/validation", "lib/chat/course", "lib/chat/progress", "lib/chat/client"]) {
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
const { ChatClientError, contextPrefix, deleteChatSession, fetchChatHistory, getChatStatus, renameChatSession, sendChatMessage, sendGuestChatMessage, sendNonStreamChatMessage } = require("./lib/chat/client.js");
const { courseToStops, parseChatCourse } = require("./lib/chat/course.js");
const json = (value, status = 200) => Response.json(value, { status });
const sse = events => new Response(new ReadableStream({
  start(controller) {
    controller.enqueue(new TextEncoder().encode(events.map(([event, data]) =>
      `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`).join("")));
    controller.close();
  },
}), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
const memberEvents = (turn = "turn-1", chunks = ["첫 ", "답변"]) => [
  ["checkpoint", { turn_id: turn, receipt: "empty" }],
  ...chunks.map((text, index) => ["delta", { turn_id: turn, receipt: `part-${index}`, text }]),
  ["done", { turn_id: turn, receipt: "complete", places: [{ name: "식당" }], coursePayload: { title: "직관 코스" }, route: "course:DOOSAN" }],
];

beforeEach(() => { stored.clear(); clearMemberTokens(); });

test("member completion uses protected direct endpoints and observable finalize ids", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const calls = [];
  global.fetch = async (url, init = {}) => {
    const body = init.body && JSON.parse(init.body);
    calls.push({ url: String(url), method: init.method, body, authorization: new Headers(init.headers).get("Authorization") });
    if (init.method === "GET") return json([]);
    if (String(url) === "/api/chat/sessions/") return json({ id: 7, title: "첫 질문", created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" }, 201);
    if (String(url).includes("/finalize/")) return json({
      turn_id: "turn-1", session_id: 7, status: body.status,
      user_message_id: 11, assistant_message_id: 12, assistant_message: body.prefix,
    });
    return sse(memberEvents());
  };
  assert.equal((await getChatStatus()).provider, "backend");
  const seen = [];
  const reply = await sendChatMessage(
    { messages: [{ role: "user", content: "첫 질문" }] }, undefined,
    { onDelta: value => seen.push(value) },
  );
  assert.deepEqual(seen, ["첫 ", "첫 답변"]);
  assert.deepEqual(
    { reply: reply.reply, status: reply.completionStatus, user: reply.userMessageId, assistant: reply.assistantMessageId },
    { reply: "첫 답변", status: "completed", user: 11, assistant: 12 },
  );
  assert.deepEqual({ places: reply.places, payload: reply.coursePayload, route: reply.route }, {
    places: [{ name: "식당" }], payload: { title: "직관 코스" }, route: "course:DOOSAN",
  });
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ["GET", "/api/chat/sessions/"], ["POST", "/api/chat/sessions/"],
    ["POST", "/api/chat/sessions/7/messages/"], ["POST", "/api/chat/turns/turn-1/finalize/"],
  ]);
  assert.ok(calls.every(call => call.authorization === "Bearer access-token"));
});

test("backend-only session history, rename, delete and JSON message functions are typed direct calls", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const calls = [];
  global.fetch = async (url, init = {}) => {
    calls.push([init.method ?? "GET", String(url), init.headers]);
    if (init.method === "DELETE") return new Response(null, { status: 204 });
    if (init.method === "PATCH") return json({ id: 7, title: "이름", created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" });
    if (String(url).endsWith("/messages/") && init.method === "GET") return json([{ id: 1, sequence_no: 1, role: "human", content: "질문", status: "", created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" }]);
    return json({ session_id: 7, user_message: "질문", assistant_message: "답", status: "completed", user_message_id: 1, assistant_message_id: 2, route: "course:DOOSAN" }, 201);
  };
  assert.equal((await renameChatSession(7, "이름")).title, "이름");
  assert.equal((await fetchChatHistory(7))[0].role, "human");
  assert.equal((await sendNonStreamChatMessage(7, "질문")).route, "course:DOOSAN");
  assert.equal(await deleteChatSession(7), undefined);
  assert.ok(calls.every(([, , headers]) => new Headers(headers).get("Authorization") === "Bearer access-token"));
});

test("member Stop freezes the last received signed prefix and is not an error", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const controller = new AbortController();
  let latest = null, frozen = null, finalized;
  global.fetch = async (url, init = {}) => {
    if (String(url).includes("/finalize/")) {
      finalized = JSON.parse(init.body);
      return json({ turn_id: "turn-stop", session_id: 9, status: "stopped", user_message_id: 21, assistant_message_id: 22, assistant_message: finalized.prefix });
    }
    return new Response(new ReadableStream({
      start(stream) {
        stream.enqueue(new TextEncoder().encode(
          'event: checkpoint\ndata: {"turn_id":"turn-stop","receipt":"empty"}\n\n' +
          'event: delta\ndata: {"turn_id":"turn-stop","receipt":"signed-partial","text":"정확한 부분"}\n\n'));
        init.signal.addEventListener("abort", () => stream.error(new DOMException("Aborted", "AbortError")), { once: true });
      },
    }), { headers: { "Content-Type": "text/event-stream" } });
  };
  const reply = await sendChatMessage(
    { sessionId: 9, messages: [{ role: "user", content: "질문" }] }, controller.signal,
    {
      onCheckpoint: checkpoint => { latest = checkpoint; },
      onDelta: () => { frozen = latest; controller.abort(); },
      getStop: () => frozen,
    },
  );
  assert.equal(reply.completionStatus, "stopped");
  assert.equal(reply.reply, "정확한 부분");
  assert.deepEqual(finalized, { receipt: "signed-partial", prefix: "정확한 부분", status: "stopped" });
});

test("member Stop requested before the initial checkpoint persists only the human message", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const controller = new AbortController();
  let frozen = null, finalized;
  global.fetch = async (url, init = {}) => {
    if (String(url) === "/api/chat/sessions/") return json({ id: 10, title: "질문", created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" }, 201);
    if (String(url).includes("/finalize/")) {
      finalized = JSON.parse(init.body);
      return json({ turn_id: "turn-empty", session_id: 10, status: "stopped", user_message_id: 31, assistant_message_id: null, assistant_message: "" });
    }
    return new Response(new ReadableStream({
      start(stream) {
        stream.enqueue(new TextEncoder().encode('event: checkpoint\ndata: {"turn_id":"turn-empty","receipt":"signed-empty"}\n\n'));
        init.signal.addEventListener("abort", () => stream.error(new DOMException("Aborted", "AbortError")), { once: true });
      },
    }), { headers: { "Content-Type": "text/event-stream" } });
  };
  const reply = await sendChatMessage(
    { messages: [{ role: "user", content: "질문" }] }, controller.signal,
    {
      onCheckpoint: checkpoint => { frozen = checkpoint; controller.abort(); },
      getStop: () => frozen,
    },
  );
  assert.equal(reply.completionStatus, "stopped");
  assert.equal(reply.reply, "");
  assert.equal(reply.assistantMessageId, null);
  assert.deepEqual(finalized, { receipt: "signed-empty", prefix: "", status: "stopped" });
});

test("member finalize failure reports uncertainty without losing the received answer", async () => {
  saveMemberTokens("access-token", "refresh-token");
  let received = "";
  global.fetch = async (url) => String(url).includes("/finalize/")
    ? json({ detail: "save unavailable" }, 500)
    : sse(memberEvents("turn-unsaved", ["받은 ", "전체 답변"]));
  await assert.rejects(
    sendChatMessage(
      { sessionId: 11, messages: [{ role: "user", content: "질문" }] },
      undefined,
      { onDelta: answer => { received = answer; } },
    ),
    error => error instanceof ChatClientError && error.uncertain,
  );
  assert.equal(received, "받은 전체 답변");
});

test("guest uses bounded browser history, Stop before a token stays local, and no auth header is sent", async () => {
  const controller = new AbortController();
  let frozen = null, body, authorization;
  global.fetch = async (url, init = {}) => {
    assert.equal(url, "/api/chat/guest/");
    body = JSON.parse(init.body); authorization = new Headers(init.headers).get("Authorization");
    return new Response(new ReadableStream({
      start(stream) { init.signal.addEventListener("abort", () => stream.error(new DOMException("Aborted", "AbortError")), { once: true }); },
    }), { headers: { "Content-Type": "text/event-stream" } });
  };
  const reply = await sendGuestChatMessage(
    { messages: [{ role: "user", content: "이전 질문" }, { role: "assistant", content: "부분" }, { role: "user", content: "후속" }] },
    controller.signal,
    {
      onCheckpoint: checkpoint => { frozen = checkpoint; controller.abort(); },
      getStop: () => frozen,
    },
  );
  assert.deepEqual(body.messages.map(item => item.content), ["이전 질문", "부분", "후속"]);
  assert.equal(authorization, null);
  assert.equal(reply.completionStatus, "stopped");
  assert.equal(reply.reply, "");
});

const coursePlaces = [
  { phase: "BEFORE", name: "상무초밥 잠실점", lat: 37.51, lng: 127.08, category: "FOOD", placeId: "123456", address: "서울 송파구", reason: "초밥", time: "16:06", stayMin: 50 },
  { phase: "BEFORE", name: "좌표 없는 곳", lat: null, lng: 127.08, category: "CAFE" },
  { phase: "GAME", name: "잠실야구장", lat: 37.512, lng: 127.072, category: "STADIUM", placeId: null, time: "17:45" },
  { phase: "AFTER", name: "잠실 게스트하우스", lat: 37.51, lng: 127.08, category: "STAY", placeId: "555", time: "22:49" },
];
const courseDone = answer => ({
  assistant_message: answer, places: coursePlaces, stadiumCode: "JAMSIL",
  travel: { mode: "car", label: "자동차", summary: "한 번 주차하고 걸어 다니면 돼요", lines: ["주차: 종합운동장 주차 — 876면"] },
  coursePayload: { title: "09-16 잠실야구장 직관 코스 (데이트)", content: "타임라인" },
});

test("guest course answer carries a map-ready course and never sends it back to the server", async () => {
  const bodies = [];
  global.fetch = async (url, init = {}) => {
    bodies.push(JSON.parse(init.body));
    return sse([["delta", { text: "코스예요" }], ["done", courseDone("코스예요")]]);
  };
  const reply = await sendGuestChatMessage({ messages: [{ role: "user", content: "차 타고 잠실 코스" }] });
  assert.equal(reply.completionStatus, "completed");
  assert.equal(reply.course.stadiumCode, "JAMSIL");
  assert.equal(reply.course.travelMode, "car");
  assert.deepEqual(reply.course.places.map(place => place.name), ["상무초밥 잠실점", "잠실야구장", "잠실 게스트하우스"]);
  assert.deepEqual(reply.course.notes, ["주차: 종합운동장 주차 — 876면"]);
  await sendGuestChatMessage({ messages: [
    { role: "user", content: "차 타고 잠실 코스" }, { role: "assistant", content: "코스예요", course: reply.course }, { role: "user", content: "고마워" },
  ] });
  assert.deepEqual(Object.keys(bodies[1].messages[1]).sort(), ["content", "role"]);
});

test("member course answer keeps the course only when the turn completed", async () => {
  saveMemberTokens("access-token", "refresh-token");
  global.fetch = async (url, init = {}) => {
    if (String(url).includes("/finalize/")) {
      const body = JSON.parse(init.body);
      return json({ turn_id: "turn-course", session_id: 5, status: body.status, user_message_id: 1, assistant_message_id: 2, assistant_message: body.prefix });
    }
    return sse([
      ["checkpoint", { turn_id: "turn-course", receipt: "empty" }],
      ["delta", { turn_id: "turn-course", receipt: "part", text: "코스" }],
      ["done", { turn_id: "turn-course", receipt: "complete", ...courseDone("코스") }],
    ]);
  };
  const reply = await sendChatMessage({ sessionId: 5, messages: [{ role: "user", content: "잠실 코스" }] });
  assert.equal(reply.course.places.length, 3);
  assert.equal(reply.course.title, "09-16 잠실야구장 직관 코스 (데이트)");
});

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

test("guest read failure is retryable while failed member auth never falls back to guest", async () => {
  global.fetch = async url => {
    if (String(url) === "/api/chat/guest/") return new Response("broken", { headers: { "Content-Type": "text/plain" } });
    throw new Error("guest fallback must not be attempted");
  };
  await assert.rejects(sendGuestChatMessage({ messages: [{ role: "user", content: "질문" }] }), error => error instanceof ChatClientError && !error.uncertain);
  await assert.rejects(getChatStatus(), error => error instanceof ChatClientError && error.status === 401);
});

test("provider blocks guest questions and clears state on every identity switch", () => {
  const provider = readFileSync(join(frontend, "components/chat-provider.tsx"), "utf8");
  const surfaces = ["components/chat-popup.tsx", "components/chat-workspace.tsx"].map(path => readFileSync(join(frontend, path), "utf8"));
  assert.match(provider, /const identity = memberStatus === "authenticated"/);
  for (const cleanup of ["controller.abort()", "backendSessions.current.clear()", "archivedConversations.current.clear()", "historyRef.current = []", "streamingRef.current = \"\""]) assert.ok(provider.includes(cleanup));
  assert.match(provider, /memberStatus !== "authenticated"/);
  assert.match(provider, /deliveryUncertain = mode === "member"/);
  assert.match(provider, /active\.wantsStop = true;[\s\S]*?if \(active\.checkpoint\)/);
  assert.match(provider, /if \(active\.wantsStop && !active\.stop\) \{ active\.stop = checkpoint; controller\.abort\(\); \}/);
  assert.match(provider, /finalizeSignal: identityController\.signal/);
  assert.doesNotMatch(provider, /localStorage|sessionStorage/);
  assert.match(provider, /messages: identityChanged \? \[\] : messages/);
  assert.match(provider, /setMessages\(\[\.\.\.previous, userMessage, \{ role: "assistant", content: received \}\]\)/);
  assert.match(provider, /받은 답변은 저장되지 않았어요/);
  for (const surface of surfaces) {
    assert.match(surface, /답변 생성 중단/);
    assert.match(surface, /로그인하고 질문하기/);
    assert.match(surface, /aria-relevant="additions"/);
  }
});

test("authenticated chat has no legacy Next cookie relay", () => {
  assert.equal(existsSync(join(frontend, "app/chat-api/route.ts")), false);
  assert.equal(existsSync(join(frontend, "lib/chat/team.ts")), false);
  assert.equal(existsSync(join(frontend, "app/baseball-admin-api/route.ts")), false);
});

test("selected stadium and map origin are sent as leading tags the backend strips", () => {
  assert.equal(contextPrefix(undefined), "");
  assert.equal(contextPrefix({ intent: "route" }), "");
  assert.equal(contextPrefix({ stadium: "잠실야구장" }), "[선택한 구장: 잠실야구장]\n");
  assert.equal(contextPrefix({ stadium: "잠실야구장", origin: { lat: 37.5, lng: 127.0712345 } }), "[선택한 구장: 잠실야구장]\n[출발지: 37.500000,127.071235]\n");
});
