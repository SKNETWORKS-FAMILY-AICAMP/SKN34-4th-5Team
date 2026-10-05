import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-provider-race-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
symlinkSync(join(frontend, "node_modules"), join(scratch, "node_modules"), "dir");

function compile(name) {
  const source = readFileSync(join(frontend, `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}

for (const name of ["lib/client-id", "lib/route-draft", "lib/stadiums", "lib/stadium-locations", "lib/community-rich-content", "lib/google-lodging", "lib/drawn-course", "lib/chat/current-course", "lib/chat/writer-state", "lib/course-directions", "lib/chat/planning", "lib/chat/types", "lib/chat/validation", "lib/chat/course", "lib/chat/history"]) compile(name);
mkdirSync(join(scratch, "components"), { recursive: true });

const providerSource = readFileSync(join(frontend, "components/chat-provider.tsx"), "utf8")
  .replace('from "react"', 'from "../test-react"')
  .replace('from "next/navigation"', 'from "../test-navigation"')
  .replaceAll('from "@/lib/chat/types"', 'from "../lib/chat/types"')
  .replace('from "@/lib/chat/client"', 'from "../test-chat-client"')
  .replace('from "@/lib/chat/history"', 'from "../lib/chat/history"')
  .replace('from "@/lib/chat/course"', 'from "../lib/chat/course"')
  .replace('from "@/lib/chat/writer-state"', 'from "../lib/chat/writer-state"')
  .replace('from "@/lib/member-auth"', 'from "../test-member-auth"')
  .replace('from "@/lib/client-id"', 'from "../test-client-id"')
  .replace('from "./chat-popup"', 'from "../test-popup"');
writeFileSync(join(scratch, "components/chat-provider.js"), ts.transpileModule(providerSource, {
  fileName: "chat-provider.tsx",
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText);

writeFileSync(join(scratch, "test-react.js"), `
exports.createContext = (...args) => global.__hooks.createContext(...args);
exports.useCallback = (...args) => global.__hooks.useCallback(...args);
exports.useContext = (...args) => global.__hooks.useContext(...args);
exports.useEffect = (...args) => global.__hooks.useEffect(...args);
exports.useRef = (...args) => global.__hooks.useRef(...args);
exports.useState = (...args) => global.__hooks.useState(...args);
`);
writeFileSync(join(scratch, "test-navigation.js"), `
exports.usePathname = () => global.__path ?? "/";
exports.useRouter = () => ({ push(url) { global.__pushes?.push(url); } });
`);
writeFileSync(join(scratch, "test-member-auth.js"), `exports.useMemberAuth = () => global.__memberAuth;`);
writeFileSync(join(scratch, "test-client-id.js"), `exports.createClientId = () => "new-chat";`);
writeFileSync(join(scratch, "components/chat-planning.js"), `exports.ChatQuestions = () => null; exports.ChatWriterOffer = () => null;`);
writeFileSync(join(scratch, "test-popup.js"), `exports.ChatPopup = () => null;`);
writeFileSync(join(scratch, "test-chat-client.js"), `
class ChatClientError extends Error { constructor(message, status, uncertain = false, sessionId, code) { super(message); Object.assign(this, {status, uncertain, sessionId, code}); } }
class ChatStreamStoppedError extends ChatClientError {}
exports.ChatClientError = ChatClientError;
exports.ChatStreamStoppedError = ChatStreamStoppedError;
exports.USAGE_BUSY = "usage_busy";
exports.GUEST_STATUS = { provider: "guest", model: "guest", ready: true };
for (const name of ["deleteChatMessages", "deleteChatSession", "editChatMessage", "fetchChatHistory", "fetchChatUsage", "getChatStatus", "listChatSessions", "sendChatMessage", "saveAnswerFeedback"])
  exports[name] = (...args) => global.__chatApi[name](...args);
`);

function hookRunner() {
  const slots = [];
  let cursor = 0;
  let pending = [];
  const same = (left, right) => left && right && left.length === right.length && left.every((value, index) => Object.is(value, right[index]));
  const hooks = {
    createContext: value => ({ value, Provider() {} }),
    useContext: context => context.value,
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[index].value, value => { slots[index].value = typeof value === "function" ? value(slots[index].value) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: { current: initial } };
      return slots[index].value;
    },
    useCallback(callback, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) slots[index] = { value: callback, dependencies };
      return slots[index].value;
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) {
        const previous = slots[index]?.cleanup;
        slots[index] = { ...slots[index], dependencies };
        pending.push(() => {
          previous?.();
          slots[index].cleanup = effect();
        });
      }
    },
  };
  global.__hooks = hooks;
  const require = createRequire(join(scratch, "entry.cjs"));
  const { ChatProvider } = require("./components/chat-provider.js");
  return {
    render() {
      global.__hooks = hooks;
      cursor = 0;
      pending = [];
      return ChatProvider({ children: null }).props.value;
    },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); },
    flushEffects() {
      const effects = pending;
      pending = [];
      effects.forEach(run => run());
    },
  };
}

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const FIRST = "3f2c1a4e-8b7d-4c21-9e0f-5a6b7c8d9e01", SECOND = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const USER_MSG = 1, ASSISTANT_MSG = 2;
const OLD_MSG = 4;
const rooms = [{ id: FIRST, title: "첫 대화" }, { id: SECOND, title: "둘째 대화" }];
const row = (id, role, content, status = "completed", tools = []) => ({ id, sequence_no: id, role, content, status, tools, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
const unused = async () => { throw new Error("not used"); };
const baseApi = {
  deleteChatMessages: unused, deleteChatSession: unused, editChatMessage: unused, sendChatMessage: unused,
  fetchChatHistory: async () => [],
  fetchChatUsage: async () => ({ active_turn: false, can_send: true }),
  getChatStatus: async mode => ({ provider: mode === "member" ? "backend" : "guest", model: "server", ready: true }),
};

test("new course draws pins once for the current stadium and survives history reconciliation", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const course = { stadiumCode: "JAMSIL", notes: [], places: [{ name: "잠실 식당", phase: "BEFORE", category: "FOOD", lat: 37.51, lng: 127.07 }] };
  let stored = [], request, applied = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatHistory: async () => stored,
    sendChatMessage: async (_, body) => {
      request = body;
      stored = [row(1, "user", body.content), { ...row(2, "assistant", "잠실 코스"), course }];
      return { reply: "잠실 코스", sessionId: FIRST, assistantMessageId: 2, provider: "guest", model: "test", ready: true, course };
    } };
  const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
  c.onContextChange({ stadium: "CHANGWON", intent: "route" });
  c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, apply: () => { applied++; return () => {}; } });
  c.onDraftChange("식사와 카페 코스"); c = runner.render(); c.onSend(); await tick(); c = runner.render();
  assert.equal(request.context.stadium, "JAMSIL");
  assert.equal(applied, 1);
  const answer = c.messages.find(message => message.role === "assistant");
  assert.equal(answer.course.stadiumCode, "JAMSIL");
  assert.ok(c.appliedCourses.get(answer.course)?.undo);
  runner.unmount();
});

test("a new stadium or team course moves the map and retains undo", async () => {
  for (const question of ["이번엔 롯데로 짜줘", "잠실 말고 사직으로 바꿔줘"]) {
    global.__memberAuth = { status: "anonymous", user: null };
    const course = { stadiumCode: "SAJIK", places: [], notes: [] };
    let applied, undone = false;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: async () => ({ reply: "사직 코스", provider: "guest", model: "test", ready: true, course }) };
    const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
    c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 4, apply: (value, how) => { applied = { value, how }; return () => { undone = true; }; } });
    c.onDraftChange(question); c = runner.render(); c.onSend(); await tick(); c = runner.render(); runner.flushEffects();
    assert.equal(applied.value.stadiumCode, "SAJIK");
    assert.equal(applied.how, "replace");
    assert.match(c.appliedCourses.get(course).message, /구장을 바꾸고/);
    c.undoChatCourse(course);
    assert.equal(undone, true);
    runner.unmount();
  }
});

test("a late course preserves a newer map selection, including switching back or remounting", async () => {
  for (const change of ["other", "back", "remount", "segment"]) {
    global.__memberAuth = { status: "anonymous", user: null };
    const pending = deferred(); let applied = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: () => pending.promise };
    const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
    c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, apply: () => { applied++; return () => {}; } });
    c.onDraftChange("코스"); c = runner.render(); c.onSend();
    c.registerCourseTarget(change === "remount" ? null : { stadiumCode: change === "segment" ? "JAMSIL" : "GOCHEOK", selectionKey: change === "segment" ? "new segment" : undefined, stopCount: 0, apply: () => { applied++; return () => {}; } });
    if (change === "back" || change === "remount") c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, apply: () => { applied++; return () => {}; } });
    pending.resolve({ reply: "코스", provider: "guest", model: "test", ready: true,
      course: { stadiumCode: "SAJIK", places: [], notes: [] } });
    await tick(); c = runner.render();
    assert.equal(applied, 0);
    runner.unmount();
  }
});

test("send reads the authoritative course immediately and late replies check its revision without waiting for registration", async () => {
  for (const changed of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null };
    const pending = deferred(); let body, applied = 0, revision = 2;
    let latest = { stadium: "SAJIK", currentCourse: { places: ["manual cafe"], writerState: { origin: null } } };
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, value) => { body = value; return pending.promise; } };
    const runner = hookRunner(); let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render();
    chat.onContextChange({ stadium: "JAMSIL", currentCourse: { places: ["stale"] } });
    chat.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, getContext: () => latest, getVersion: () => String(revision),
      apply: () => { applied++; return () => true; } });
    chat.onDraftChange("카페만 바꿔줘"); chat = runner.render(); chat.onSend();
    assert.deepEqual(body.context, latest);
    if (changed) { revision++; latest = { stadium: "SAJIK", currentCourse: { places: [], writerState: { origin: null } } }; }
    pending.resolve({ reply: "카페 변경", provider: "guest", model: "test", ready: true, course: { stadiumCode: "SAJIK", places: [], notes: [] } });
    await tick(); chat = runner.render();
    assert.equal(applied, changed ? 0 : 1);
    runner.unmount();
  }
});

test("a rejected stale undo is reported as preserving the newer course", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  global.__chatApi = { ...baseApi, listChatSessions: async () => [] };
  const runner = hookRunner(); let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render();
  const course = { stadiumCode: "JAMSIL", places: [], notes: [] };
  chat.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 1, apply: () => () => false });
  chat.applyChatCourse(course, "replace"); chat = runner.render(); runner.flushEffects();
  chat.undoChatCourse(course); chat = runner.render();
  assert.match(chat.appliedCourses.get(course).message, /최신 내용을 유지/);
  assert.equal(chat.appliedCourses.get(course).undo, null);
  runner.unmount();
});

global.window = {
  location: { pathname: "/", search: "", hash: "", origin: "http://localhost" }, scrollY: 0,
  setTimeout: (callback, delay) => { if (delay === 1200) global.__queueTimer = callback; return 1; }, clearTimeout() {}, setInterval: callback => { global.__timer = callback; return 1; }, clearInterval() { global.__timer = null; },
  addEventListener() {}, removeEventListener() {},
  scrollTo() {}, matchMedia: () => ({ matches: false }),
};
global.requestAnimationFrame = callback => { callback(); return 1; };
global.cancelAnimationFrame = () => {};
global.__memberAuth = { status: "authenticated", user: { id: 7 } };

test("provider ignores delayed list/history callbacks and reloads an interrupted room", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  const lateList = deferred();
  global.__chatApi = { ...baseApi, listChatSessions: () => lateList.promise };
  let runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  controls.onDraftChange("작성 중");
  lateList.resolve(rooms);
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, "initial-chat");
  assert.equal(controls.draft, "작성 중");
  assert.deepEqual(controls.conversations, [{ id: "initial-chat", title: "새 대화" }]);

  const firstHistory = deferred(), secondHistory = deferred();
  const historyCalls = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => rooms,
    fetchChatHistory: (mode, sessionId) => {
      historyCalls.push([mode, sessionId]);
      if (sessionId === FIRST && historyCalls.filter(([, id]) => id === FIRST).length === 1) return firstHistory.promise;
      if (sessionId === SECOND) return secondHistory.promise;
      return Promise.resolve([]);
    },
  };
  runner = hookRunner();
  controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, `member:${FIRST}`);

  controls.onSelectConversation(`member:${SECOND}`);
  controls = runner.render();
  controls.onDraftChange("둘째 방 초안");
  firstHistory.resolve([row(OLD_MSG, "user", "늦은 첫 기록")]);
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, `member:${SECOND}`);
  assert.equal(controls.draft, "둘째 방 초안");

  secondHistory.resolve([row(USER_MSG, "user", "둘째 질문"), row(ASSISTANT_MSG, "assistant", "둘째 답변")]);
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.messages.map(message => [message.id, message.role, message.content]), [[USER_MSG, "user", "둘째 질문"], [ASSISTANT_MSG, "assistant", "둘째 답변"]]);
  assert.equal(controls.draft, "둘째 방 초안");

  controls.onSelectConversation(`member:${FIRST}`);
  assert.deepEqual(historyCalls, [["member", FIRST], ["member", SECOND], ["member", FIRST]]);
});

test("feedback updates shared history, rejects duplicate writes and ignores an old identity", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  let pendingVote = deferred();
  const calls = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [rooms[0]],
    fetchChatHistory: async () => [row(USER_MSG, "user", "질문"), row(ASSISTANT_MSG, "assistant", "답변")],
    saveAnswerFeedback: (...args) => { calls.push(args); return pendingVote.promise; },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  const up = { rating: "up", reason: "", comment: "" };
  const saving = controls.onFeedback(ASSISTANT_MSG, up);
  await assert.rejects(controls.onFeedback(ASSISTANT_MSG, up));
  pendingVote.resolve(up);
  await saving;
  controls = runner.render();
  assert.deepEqual(controls.messages.find(message => message.id === ASSISTANT_MSG).feedback, up);
  assert.deepEqual(calls, [["member", FIRST, ASSISTANT_MSG, up]]);
  pendingVote = deferred();
  const cancelling = controls.onFeedback(ASSISTANT_MSG, null);
  global.__memberAuth = { status: "anonymous", user: null };
  controls = runner.render();
  runner.flushEffects();
  pendingVote.resolve(null);
  await cancelling;
  await tick();
  controls = runner.render();
  assert.ok(controls.messages.every(message => !message.feedback));
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

test("colliding answer IDs vote independently across conversations and account changes", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  const votes = [];
  global.__chatApi = {
    ...baseApi, listChatSessions: async () => rooms,
    fetchChatHistory: async () => [row(1, "user", "question"), row(2, "assistant", "answer")],
    saveAnswerFeedback: (...args) => { const pending = deferred(); votes.push({ args, pending }); return pending.promise; },
  };
  const runner = hookRunner();
  let controls = runner.render(); runner.flushEffects(); await tick(); controls = runner.render();
  const up = { rating: "up", reason: "", comment: "" };
  const first = controls.onFeedback(2, up);
  controls.onSelectConversation(`member:${SECOND}`); await tick(); controls = runner.render();
  const second = controls.onFeedback(2, up);
  assert.equal(votes.length, 2);
  votes[0].pending.resolve(up); await first;
  await assert.rejects(controls.onFeedback(2, up));
  global.__memberAuth = { status: "authenticated", user: { id: 8 } };
  controls = runner.render(); runner.flushEffects(); await tick(); controls = runner.render();
  controls.onSelectConversation(`member:${SECOND}`); await tick(); controls = runner.render();
  const newOwner = controls.onFeedback(2, up);
  assert.equal(votes.length, 3);
  votes[1].pending.resolve(up); await second;
  controls = runner.render();
  assert.ok(controls.messages.every(message => !message.feedback));
  await assert.rejects(controls.onFeedback(2, up));
  votes[2].pending.resolve(up); await newOwner;
});

test("assistant deletion removes only its answer and reloads a truthful question tombstone", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  let rows = [row(1, "user", "question"), row(2, "assistant", "answer"), row(3, "user", "later"), row(4, "assistant", "later answer")];
  const confirmations = [];
  window.confirm = text => { confirmations.push(text); return true; };
  global.__chatApi = {
    ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => rows,
    deleteChatMessages: async (_mode, _session, id) => { rows = id === 2 ? [{ ...rows[0], answer_deleted: true }, ...rows.slice(2)] : rows.slice(0, rows.findIndex(item => item.id === id)); },
  };
  const runner = hookRunner();
  let controls = runner.render(); runner.flushEffects(); await tick(); controls = runner.render();
  controls.onDeleteMessage(2); await tick(); controls = runner.render();
  assert.deepEqual(controls.messages.map(message => message.id), [1, 3, 4]);
  assert.equal(controls.messages[0].answerDeleted, true);
  assert.match(confirmations[0], /이 답변만/);
  assert.match(confirmations[0], /질문과 다른 대화는 그대로/);
  controls.onDeleteMessage(3); await tick(); controls = runner.render();
  assert.deepEqual(controls.messages.map(message => message.id), [1]);
  assert.match(confirmations[1], /이 질문과 이후 대화를 모두/);
});

test("guest reload lists cookie-owned sessions and restores history in guest mode", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const calls = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async mode => { calls.push(["list", mode]); return [rooms[0]]; },
    fetchChatHistory: async (mode, sessionId) => { calls.push(["history", mode, sessionId]); return [row(USER_MSG, "user", "비회원 질문"), row(ASSISTANT_MSG, "assistant", "비회원 답")]; },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, `guest:${FIRST}`);
  assert.deepEqual(controls.messages.map(message => message.content), ["비회원 질문", "비회원 답"]);
  assert.ok(calls.some(call => call[0] === "list" && call[1] === "guest"));
  assert.deepEqual(calls.filter(call => call[0] === "history"), [["history", "guest", FIRST]]);
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

test("history reload surfaces a stopped turn's status without dropping it", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [rooms[0]],
    fetchChatHistory: async () => [row(USER_MSG, "user", "질문", "stopped"), row(ASSISTANT_MSG, "assistant", "", "stopped")],
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.messages.map(message => [message.id, message.status]), [[USER_MSG, "stopped"], [ASSISTANT_MSG, "stopped"]]);
});

test("live delta/tool events keep SSE order, update tools in place, and clear once the turn settles", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  let onTool, onDelta, release;
  const persisted = [row(USER_MSG, "user", "잠실 맛집 알려 주세요"), row(ASSISTANT_MSG, "assistant", "답변", "completed", [{ id: "call-1", tool_name: "search_places", status: "completed" }])];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [],
    fetchChatHistory: async () => persisted,
    sendChatMessage: (mode, body, signal, callbacks) => { ({ onTool, onDelta } = callbacks); return new Promise(resolve => {
      onDelta("찾아"); onDelta("볼게요");
      onTool({ id: "call-1", tool_name: "search_places", status: "running" });
      onTool({ id: "call-1", tool_name: "search_places", status: "completed" });
      onDelta(" 부분");
      release = () => resolve({ reply: "답변", sessionId: FIRST, provider: "guest", model: "m", ready: true, assistantMessageId: ASSISTANT_MSG, tools: [{ id: "call-1", toolName: "search_places", status: "completed", kind: "tool", parentId: null }] });
    }); },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  controls.onDraftChange("잠실 맛집 알려 주세요");
  controls = runner.render();
  controls.onSend();
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.timeline, [
    { kind: "text", text: "찾아볼게요", parentId: null },
    { kind: "tools", tools: [{ id: "call-1", toolName: "search_places", status: "completed", kind: "tool", parentId: null }] },
    { kind: "text", text: " 부분", parentId: null },
  ]);
  release();
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.timeline, []);
  assert.equal(controls.messages.at(-1).content, "답변");
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.messages.at(-1).tools, [{ id: "call-1", toolName: "search_places", status: "completed", kind: "tool", parentId: null }]);
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

test("Stop aborts only the local stream, clears loading and shows the unsaved notice", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const sends = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [],
    sendChatMessage: (mode, body, signal) => {
      sends.push([mode, body.content]);
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("요청이 중단됐어요.")), { once: true }));
    },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  controls.onDraftChange("잠실 맛집 알려 주세요");
  controls = runner.render();
  controls.onSend();
  controls = runner.render();
  assert.equal(controls.pending, "잠실 맛집 알려 주세요");
  controls.onCancel();
  await tick();
  controls = runner.render();
  assert.equal(controls.pending, "");
  assert.equal(controls.failed, "");
  assert.equal(controls.error, "");
  assert.equal(controls.notice, "답변 받기를 중단했어요. 받던 답변은 저장되지 않아요.");
  assert.equal(controls.draft, "잠실 맛집 알려 주세요");
  assert.deepEqual(sends, [["guest", "잠실 맛집 알려 주세요"]]);
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

// Stop clears `pending` inside its own click, so React reuses that <button> node as the submit/send button.
// Without preventDefault the browser's default activation then re-submits the restored draft (seen in the real UI).
for (const [path, exportName] of [["components/chat-workspace", "ChatWorkspace"], ["components/chat-popup", "ChatPopup"]]) {
  test(`${exportName} stop button cancels without the default submit activation`, () => {
    const source = readFileSync(join(frontend, `${path}.tsx`), "utf8")
      .replace(/^import "@\/styles\/[^"]+";$/m, "")
      .replace('from "react"', 'from "../test-surface-react"')
      .replace('from "next/link"', 'from "../test-surface-stub"')
      .replace('from "next/image"', 'from "../test-surface-stub"')
      .replace('from "@/lib/chat/types"', 'from "../lib/chat/types"')
      .replace('from "@/lib/member-auth"', 'from "../test-member-auth"')
      .replace(/from "\.\/(chat-provider|icons|chat-answer|chat-course-card|chat-course-preferences|chat-queue|chat-pending|chat-progress|chat-usage|chat-feedback)"/g, 'from "../test-surface-stub"');
    writeFileSync(join(scratch, `${path}.js`), ts.transpileModule(source, {
      fileName: `${path}.tsx`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText);
    writeFileSync(join(scratch, "test-surface-react.js"), "exports.useEffect = () => {}; exports.useRef = current => ({ current }); exports.useState = value => [value, () => {}];");
    writeFileSync(join(scratch, "test-surface-stub.js"), "const Stub = () => null; module.exports = new Proxy({ __esModule: true, default: Stub, useChat: () => global.__chat }, { get: (target, key) => key in target ? target[key] : Stub });");
    global.__memberAuth = { status: "anonymous", user: null };
    let cancelled = 0;
    global.__chat = new Proxy({
      messages: [], conversations: [{ id: "initial-chat", title: "새 대화" }], activeConversationId: "initial-chat",
      draft: "잠실 맛집 알려 주세요", pending: "잠실 맛집 알려 주세요", streaming: "", timeline: [], failed: "", error: "", notice: "",
      editingMessageId: null, status: { provider: "guest", model: "m", ready: true }, statusLoading: false, statusError: "",
      queued: [], editingQueuedId: null,
      onCancel: () => { cancelled += 1; },
    }, { get: (target, key) => key in target ? target[key] : () => {} });
    const require = createRequire(join(scratch, "entry.cjs"));
    const Surface = require(`./${path}.js`)[exportName];
    const find = node => {
      if (!node || typeof node !== "object") return null;
      if (Array.isArray(node)) { for (const child of node) { const hit = find(child); if (hit) return hit; } return null; }
      if (node.props?.["aria-label"] === "답변 생성 중단") return node;
      return find(node.props?.children);
    };
    const stop = find(Surface({}));
    assert.ok(stop, "stop button renders while a reply is pending");
    let prevented = false;
    stop.props.onClick({ preventDefault: () => { prevented = true; } });
    assert.equal(cancelled, 1);
    assert.equal(prevented, true);
    global.__chat.pending = "";
    global.__chat.messages = [{ id: 1, role: "user", content: "question", status: "completed" }, { id: 2, role: "assistant", content: "answer", status: "completed" }];
    const actions = [];
    const feedback = [];
    const collect = node => {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) { node.forEach(collect); return; }
      if (["이 질문 수정", "이 질문부터 삭제", "이 답변만 삭제"].includes(node.props?.["aria-label"])) actions.push(node);
      if (node.type === require("./test-surface-stub.js").ChatFeedback && node.props?.message?.role === "assistant") feedback.push(node);
      collect(node.props?.children);
    };
    collect(Surface({}));
    assert.deepEqual(actions.map(node => node.props["aria-label"]), ["이 질문 수정", "이 질문부터 삭제"]);
    assert.equal(feedback.length, 1, "assistant actions are delegated to one shared feedback toolbar");
    assert.equal(feedback[0].props.disabled, false);
    for (const action of actions) {
      assert.equal(action.props.type, "button");
      assert.equal(action.props.children.type, "svg");
      assert.equal(action.props.children.props["aria-hidden"], "true");
      assert.equal(action.props.children.props.width, "16");
    }
    global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  });
}

test("uncertain failure whose turn history shows completed clears failure state and only the auto-restored draft", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  for (const newer of [null, "새 질문", "잠실 맛집"]) {
    const history = deferred();
    global.__chatApi = {
      ...baseApi,
      listChatSessions: async () => [],
      fetchChatHistory: () => history.promise,
      sendChatMessage: async () => { const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js"); const error = new ChatClientError("연결이 끊겼어요."); error.sessionId = FIRST; throw error; },
    };
    const runner = hookRunner();
    let controls = runner.render();
    runner.flushEffects();
    await tick();
    controls = runner.render();
    controls.onDraftChange("잠실 맛집");
    controls = runner.render();
    controls.onSend();
    await tick();
    controls = runner.render();
    assert.equal(controls.failed, "잠실 맛집");
    assert.equal(controls.draft, "잠실 맛집");
    if (newer) { controls.onDraftChange(newer); controls = runner.render(); }
    history.resolve([row(USER_MSG, "user", "잠실 맛집"), row(ASSISTANT_MSG, "assistant", "답변")]);
    await tick();
    controls = runner.render();
    assert.deepEqual([controls.failed, controls.error, controls.notice, controls.draft], ["", "", "", newer ?? ""]);
    assert.equal(controls.messages.at(-1).content, "답변");
  }
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});


test("fresh planning offer navigates once, preserves request/conversation and cancels on stay, typing, send, route and room changes", async () => {
  const payload = { offer_writer: true, questions: [{ question: "동행", choices: ["혼자", "친구"] }] };
  const realNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    for (const action of ["auto", "immediate", "stay", "typing", "send", "route", "switch", "unmount", "close", "writer", "history"]) {
      global.__pushes = [];
      global.__path = action === "writer" ? "/routes/new" : "/chat";
      global.__memberAuth = { status: "anonymous", user: null };
      let calls = 0;
      global.__chatApi = { ...baseApi,
        listChatSessions: async () => action === "history" ? [rooms[0]] : [],
        fetchChatHistory: async () => action === "history" ? [{ ...row(2, "assistant", "조건을 알려주세요"), planning: payload }] : [],
        sendChatMessage: async (mode, body, signal, callbacks) => {
          calls++; callbacks.onPlanning(payload);
          return { reply: "조건을 알려주세요", assistantMessageId: 2, planning: payload, ready: true, model: "test", provider: "guest" };
        },
      };
      const runner = hookRunner();
      let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render(); runner.flushEffects();
      if (action !== "history") { chat.onDraftChange("야구 여행 계획 짜줘"); chat = runner.render(); chat.onSend(); await tick(); chat = runner.render(); runner.flushEffects(); }
      assert.equal(chat.writerSeconds, ["writer", "history"].includes(action) ? null : 20);
      const originalId = chat.activeConversationId;
      const announcement = chat.writerAnnouncement;
      if (chat.writerSeconds !== null) {
        assert.match(announcement, /20초 후 루트 작성 화면/);
        assert.match(announcement, /자동 이동을 취소/);
        now += 1000; global.__timer?.(); chat = runner.render();
        assert.equal(chat.writerSeconds, 19);
        assert.equal(chat.writerAnnouncement, announcement);
        assert.deepEqual(global.__pushes, []);
      }
      if (action === "immediate") { chat.goToWriter(); chat.goToWriter(); }
      if (action === "stay") chat.stayHere(true);
      if (action === "typing") chat.onDraftChange("새 초안");
      if (action === "send") chat.onSend();
      if (action === "route") { global.__path = "/routes"; chat = runner.render(); runner.flushEffects(); }
      if (action === "switch") { chat.onReset(); chat = runner.render(); runner.flushEffects(); }
      if (action === "unmount") runner.unmount();
      if (action === "close") chat.onClosePopup();
      now += 19000; global.__timer?.(); chat = runner.render();
      assert.deepEqual(global.__pushes, ["auto", "immediate"].includes(action) ? ["/routes/new"] : []);
      if (["auto", "immediate"].includes(action)) {
        assert.equal(chat.activeConversationId, originalId);
        assert.equal(chat.messages[0].content, "야구 여행 계획 짜줘");
        assert.equal(calls, 1);
      }
      if (action === "typing") assert.equal(chat.draft, "새 초안");
      if (action === "stay") assert.equal(chat.writerAnnouncement, "자동 이동을 취소했어요. 여기서 대화를 이어가요.");
      else assert.doesNotMatch(chat.writerAnnouncement, /자동 이동을 취소했어요/);
      chat.stayHere();
    }
  } finally { Date.now = realNow; global.__path = "/"; }
});


test("question group uses existing user flow once and leaves subsequent history groups disabled", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const payload = { offer_writer: false, questions: [{ question: "동행", choices: ["혼자", "친구"] }] };
  const calls = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]],
    fetchChatHistory: async () => [{ ...row(2, "assistant", "조건"), planning: payload }],
    sendChatMessage: async (_, body) => { calls.push(body.content); return { reply: "계획", assistantMessageId: 4, ready: true, model: "test", provider: "guest" }; },
  };
  const runner = hookRunner(); let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render(); runner.flushEffects();
  chat.submitQuestions(2, "동행: 친구"); chat.submitQuestions(2, "동행: 혼자"); await tick(); chat = runner.render(); chat.submitQuestions(2, "동행: 혼자");
  assert.deepEqual(calls, ["동행: 친구"]);
});

test("new identity receives its first live planning offer", async () => {
  const payload = { offer_writer: true, questions: [] };
  global.__memberAuth = { status: "authenticated", user: { id: 9002 } };
  global.__path = "/chat"; global.__pushes = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: async (_, body, signal, cb) => {cb.onPlanning(payload); return {reply:"plan",assistantMessageId:2,planning:payload,ready:true,model:"test",provider:"guest"};} };
  const runner = hookRunner(); let c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();
  c.onDraftChange("member plan");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,20);c.stayHere();
  global.__memberAuth={status:"anonymous",user:null};
  c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();
  assert.equal(c.activeConversationId,"initial-chat");
  c.onDraftChange("guest first plan");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();
  try {assert.equal(c.writerSeconds,20);} finally {runner.unmount();}
});

test("stop and stream error cancel existing live offer", async () => {
  const payload = {offer_writer:true,questions:[]};
  for(const action of ["stop","error"]){
    global.__memberAuth={status:"anonymous",user:null};global.__path="/chat";global.__pushes=[];
    let rejectSend;
    global.__chatApi={...baseApi,listChatSessions:async()=>[],sendChatMessage:async(_,body,signal,cb)=>{cb.onPlanning(payload);return new Promise((resolve,reject)=>{rejectSend=reject;signal.addEventListener("abort",()=>reject(new Error("aborted")),{once:true});});}};
    const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();c.onDraftChange("plan");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,20);
    if(action==="stop")c.onCancel();else rejectSend(new Error("local failure"));
    await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,null);assert.deepEqual(global.__pushes,[]);runner.unmount();
  }
});

test("review repro: deleting follow-up leaves question group permanently rejected", async () => {
 global.__memberAuth={status:"anonymous",user:null};
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 const original=[{...row(2,"assistant","조건"),planning:payload}];
 let stored=original, sends=0;
 global.window.confirm=()=>true;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>stored,
 sendChatMessage:async()=>{sends++;stored=[...original,row(3,"user","동행: 친구"),row(4,"assistant","계획")];return {reply:"계획",sessionId:FIRST,assistantMessageId:4,ready:true,model:"test",provider:"guest"};},
 deleteChatMessages:async()=>{stored=original;}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();
 c.submitQuestions(2,"동행: 친구");await tick();c=runner.render();
 assert.equal(c.messages.at(-1).id,4);
 c.onDeleteMessage(3);await tick();c=runner.render();
 assert.equal(c.messages.at(-1).id,2);
 c.submitQuestions(2,"동행: 혼자");await tick();c=runner.render();
 assert.equal(sends,2);assert.equal(c.pending,"");
 runner.unmount();
});
test("review repro: failed request cannot change and resend question answers",async()=>{
 global.__memberAuth={status:"anonymous",user:null};
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 let sends=0;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>[{...row(2,"assistant","조건"),planning:payload}],sendChatMessage:async()=>{sends++;throw new Error("offline");}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();
 c.submitQuestions(2,"동행: 친구");await tick();c=runner.render();assert.equal(c.error,"offline");
 c.submitQuestions(2,"동행: 혼자");await tick();c=runner.render();assert.equal(sends,2);
 runner.unmount();
});

test("planning preserves a preexisting equal composer after completed history reconciliation", async () => {
 global.__memberAuth={status:"anonymous",user:null};
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 const original=[{...row(2,"assistant","조건"),planning:payload}]; let calls=0;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>++calls===1?original:[...original,row(3,"user","동행: 친구"),row(4,"assistant","계획")],sendChatMessage:async()=>{ const {ChatClientError}=createRequire(join(scratch,"entry.cjs"))("./test-chat-client.js"); const e=new ChatClientError("offline");e.sessionId=FIRST;throw e; }};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();c.onDraftChange("동행: 친구");c=runner.render();
 assert.equal(await c.submitQuestions(2,"동행: 친구"),"failed");await tick();c=runner.render();assert.equal(c.draft,"동행: 친구");assert.equal(c.failed,"");runner.unmount();
});
test("planning rejects invalid, loading, identity and concurrent submissions truthfully", async()=>{
 global.__memberAuth={status:"anonymous",user:null}; const load=deferred(), send=deferred();let calls=0;
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:()=>load.promise,sendChatMessage:()=>{calls++;return send.promise;}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();assert.equal(await c.submitQuestions(2,"답"),"rejected");
 load.resolve([{...row(2,"assistant","조건"),planning:payload}]);await tick();c=runner.render();
 for(const [id,text] of [[99,"답"],[2," "],[2,"x".repeat(2001)]])assert.equal(await c.submitQuestions(id,text),"rejected");
 let acceptedCount=0;
 const accepted=c.submitQuestions(2,"동행: 친구",()=>acceptedCount++);assert.equal(acceptedCount,1);assert.equal(await c.submitQuestions(2,"동행: 혼자",()=>acceptedCount++),"rejected");assert.equal(acceptedCount,1);assert.equal(calls,1);
 send.resolve({reply:"계획",assistantMessageId:4,ready:true,model:"test",provider:"guest"});assert.equal(await accepted,"succeeded");
 global.__memberAuth={status:"authenticated",user:{id:88}};runner.render();runner.flushEffects();assert.equal(await c.submitQuestions(2,"동행: 혼자"),"rejected");runner.unmount();
});
test("saved failed planning answer retries via edit without duplicate user history",async()=>{
 global.__memberAuth={status:"anonymous",user:null};global.window.confirm=()=>true;
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};const original=[{...row(2,"assistant","조건"),planning:payload}];let stored=original,sends=0,edits=0;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>stored,sendChatMessage:async()=>{sends++;stored=[...original,row(3,"user","동행: 친구","failed")];throw new Error("offline");},editChatMessage:async(_,body)=>{edits++;assert.equal(body.messageId,3);stored=[...original,row(3,"user",body.content),row(4,"assistant","계획")];return {reply:"계획",sessionId:FIRST,assistantMessageId:4,ready:true,model:"test",provider:"guest"};}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();assert.equal(await c.submitQuestions(2,"동행: 친구"),"failed");await tick();c=runner.render();assert.equal(await c.submitQuestions(2,"동행: 혼자"),"rejected");c.onRetry();await tick();c=runner.render();assert.deepEqual([sends,edits],[1,1]);assert.equal(c.messages.filter(m=>m.role==="user").length,1);runner.unmount();
});

test("edit and delete start cancel writer countdown including delete failure and declined confirmation",async()=>{
 for(const action of ["edit","delete","decline"]){
  global.__memberAuth={status:"anonymous",user:null};global.__path="/chat";global.__pushes=[];global.window.confirm=()=>action!=="decline";
  const payload={offer_writer:true,questions:[]};let stored=[];
  global.__chatApi={...baseApi,listChatSessions:async()=>[],fetchChatHistory:async()=>stored,sendChatMessage:async(_,body,signal,cb)=>{cb.onPlanning(payload);stored=[row(1,"user",body.content),row(2,"assistant","계획")];return {reply:"계획",sessionId:FIRST,assistantMessageId:2,ready:true,model:"test",provider:"guest"};},deleteChatMessages:async()=>{throw new Error("delete offline");}};
  const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();c.onDraftChange("계획");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,20);
  if(action==="edit")c.onEditMessage(1);else c.onDeleteMessage(1);await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,null);assert.deepEqual(global.__pushes,[]);if(action==="delete")assert.equal(c.error,"delete offline");runner.unmount();
 }
 global.__path="/";
});

const queueReply = text => ({ reply: text, ready: true, model: "test", provider: "guest" });
async function renderEffects(runner) {
  global.__path = "/chat";
  let chat;
  for (let i = 0; i < 3; i++) { chat = runner.render(); runner.flushEffects(); await tick(); }
  return runner.render();
}
function typeAndSend(runner, text) {
  runner.render().onDraftChange(text);
  runner.render().onSend();
  return runner.render();
}

test("queues at most two follow-ups, keeps the active answer, and sends each exactly once in order", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const waiting = [deferred(), deferred(), deferred()], sent = [], signals = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body, signal, cb) => {
    const index = sent.length; sent.push(body.content); signals.push(signal); cb.onDelta("답변 중", null); return waiting[index].promise;
  } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "첫 질문"); typeAndSend(runner, "두 번째"); typeAndSend(runner, "세 번째");
  let chat = typeAndSend(runner, "한도 밖 질문");
  assert.deepEqual(chat.queued.map(q => q.content), ["두 번째", "세 번째"]);
  assert.equal(chat.draft, "한도 밖 질문"); assert.match(chat.notice, /최대 2개/); assert.equal(signals[0].aborted, false);
  waiting[0].resolve(queueReply("첫 답변 유지")); chat = await renderEffects(runner);
  assert.deepEqual(sent, ["첫 질문", "두 번째"]); assert.equal(chat.messages[1].content, "첫 답변 유지");
  assert.equal(chat.draft, "한도 밖 질문"); assert.equal(chat.queued.length, 1);
  waiting[1].resolve(queueReply("둘째 답변")); await renderEffects(runner);
  assert.deepEqual(sent, ["첫 질문", "두 번째", "세 번째"]);
  waiting[2].resolve(queueReply("셋째 답변")); chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 0); assert.equal(chat.messages.length, 6); runner.unmount();
});

test("editing a queued question holds dispatch, preserves its position and restores unrelated composer text", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(), sent = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body) => {
    sent.push(body.content); return sent.length === 1 ? first.promise : Promise.resolve(queueReply("수정 반영"));
  } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "현재 질문"); typeAndSend(runner, "취소할 질문"); let chat = typeAndSend(runner, "고칠 질문");
  const [cancelled, edited] = chat.queued; chat.onDraftChange("작성 중인 초안"); chat = runner.render(); chat.onEditQueued(edited.id);
  first.resolve(queueReply("원래 답변")); chat = await renderEffects(runner);
  assert.deepEqual(sent, ["현재 질문"]); assert.equal(chat.draft, "고칠 질문");
  chat.onRemoveQueued(cancelled.id); chat.onDraftChange("고친 예약 질문"); runner.render().onSend();
  chat = await renderEffects(runner);
  assert.deepEqual(sent, ["현재 질문", "고친 예약 질문"]); assert.equal(chat.draft, "작성 중인 초안");
  assert.equal(chat.editingQueuedId, null); runner.unmount();
});

test("cancel queued edit leaves original text; cancelling the queued item never aborts the active answer", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(); let signal;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body, value) => { signal = value; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "현재"); let chat = typeAndSend(runner, "원래 예약"); const id = chat.queued[0].id;
  chat.onEditQueued(id); runner.render().onDraftChange("저장 안 한 수정"); runner.render().onCancelQueuedEdit(); chat = runner.render();
  assert.equal(chat.queued[0].content, "원래 예약"); chat.onRemoveQueued(id); assert.equal(signal.aborted, false);
  first.resolve(queueReply("완료")); chat = await renderEffects(runner); assert.equal(chat.queued.length, 0); runner.unmount();
});

test("server busy keeps an unsent request queued and waits for usage release instead of displaying an error", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
  let attempts = 0, active = true;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
    sendChatMessage: async () => { if (++attempts === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy"); return queueReply("자동 재전송 완료"); } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "다른 답변 뒤에 보내줘"); let chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 1); assert.equal(chat.error, ""); assert.equal(chat.failed, ""); assert.equal(attempts, 1);
  assert.equal(chat.messages.length, 0); assert.equal(chat.queueWaitingForServer, true);
  active = false; global.__queueTimer(); chat = await renderEffects(runner);
  assert.equal(chat.queueWaitingForServer, false);
  assert.equal(attempts, 2); assert.equal(chat.queued.length, 0); assert.equal(chat.messages.at(-1).content, "자동 재전송 완료"); runner.unmount();
});

test("retry of a stored failed question queues the same PUT and does not duplicate questions or confirmations", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
  let stored = [], sends = 0, edits = 0, confirms = 0, active = true;
  global.window.confirm = () => { confirms++; return true; };
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatHistory: async () => stored,
    fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
    sendChatMessage: async (_, body) => {
      sends++; stored = [row(1, "user", body.content, "failed")];
      throw new ChatClientError("offline", 503, false, FIRST);
    },
    editChatMessage: async (_, body) => {
      assert.equal(body.messageId, 1); assert.equal(body.sessionId, FIRST);
      if (++edits === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy");
      stored = [row(1, "user", body.content), row(2, "assistant", "코스 완료")];
      return { ...queueReply("코스 완료"), sessionId: FIRST, assistantMessageId: 2 };
    } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "스테이크 카페 경기 호텔 코스"); let chat = await renderEffects(runner);
  assert.equal(chat.messages[0].status, "failed"); chat.onRetry(); chat = await renderEffects(runner);
  assert.equal(chat.error, ""); assert.equal(chat.failed, ""); assert.equal(chat.queued[0].edit.messageId, 1);
  assert.equal(chat.messages.length, 1); assert.equal(edits, 1);
  active = false; global.__queueTimer(); chat = await renderEffects(runner);
  assert.deepEqual([sends, edits, confirms], [1, 2, 1]); assert.equal(chat.queued.length, 0);
  assert.equal(chat.messages.filter(message => message.role === "user").length, 1);
  assert.equal(chat.messages.at(-1).content, "코스 완료"); runner.unmount();
});

test("busy queued edits preserve their target when changed and leave history intact when cancelled", async () => {
  for (const cancel of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null }; global.window.confirm = () => true;
    const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
    const original = [row(1, "user", "기존 질문"), row(2, "assistant", "기존 답변")];
    let stored = original, active = true; const edits = [];
    global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => stored,
      fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
      editChatMessage: async (_, body) => {
        edits.push(body);
        if (edits.length === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy");
        stored = [row(1, "user", body.content), row(2, "assistant", "수정 답변")];
        return { ...queueReply("수정 답변"), sessionId: FIRST };
      } };
    const runner = hookRunner(); let chat = await renderEffects(runner);
    chat.onEditMessage(1); typeAndSend(runner, "수정 질문"); chat = await renderEffects(runner);
    assert.deepEqual(chat.messages.map(message => message.content), ["기존 질문", "기존 답변"]);
    const id = chat.queued[0].id; chat.onEditQueued(id); chat = await renderEffects(runner);
    chat.onDraftChange("예약에서 고친 질문"); runner.render().onSend();
    chat = await renderEffects(runner); assert.equal(chat.queued[0].edit.messageId, 1);
    if (cancel) chat.onRemoveQueued(id);
    active = false; global.__queueTimer(); chat = await renderEffects(runner);
    assert.equal(edits.length, cancel ? 1 : 2); assert.equal(chat.queued.length, 0);
    if (cancel) assert.deepEqual(stored, original);
    else { assert.equal(edits[1].messageId, 1); assert.equal(edits[1].content, "예약에서 고친 질문"); }
    runner.unmount();
  }
});

test("queued edits pause for changed or deleted history and never replace newly arrived answers without consent", async () => {
  for (const removed of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null }; global.window.confirm = () => true;
    const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
    let stored = [row(1, "user", "기존 질문", "failed")], active = true, edits = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => stored,
      fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
      editChatMessage: async (_, body) => {
        if (++edits === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy");
        assert.equal(body.messageId, 1);
        stored = [row(1, "user", body.content), row(2, "assistant", "다시 받은 답변")];
        return { ...queueReply("다시 받은 답변"), sessionId: FIRST };
      } };
    const runner = hookRunner(); let chat = await renderEffects(runner);
    chat.onEditMessage(1); typeAndSend(runner, "수정 질문"); chat = await renderEffects(runner);
    stored = removed ? [] : [row(1, "user", "기존 질문"), row(2, "assistant", "다른 탭에서 받은 답변")];
    active = false; global.__queueTimer(); chat = await renderEffects(runner);
    assert.equal(chat.queuePaused, true); assert.equal(edits, 1); assert.equal(chat.queued.length, 1);
    global.window.confirm = () => false; chat.onResumeQueue(); chat = await renderEffects(runner);
    assert.equal(chat.queuePaused, true); assert.equal(edits, 1);
    if (removed) {
      assert.match(chat.notice, /질문이 없어졌어요/); chat.onRemoveQueued(chat.queued[0].id);
    } else {
      assert.equal(chat.messages.at(-1).content, "다른 탭에서 받은 답변");
      global.window.confirm = () => true; chat.onResumeQueue(); chat = await renderEffects(runner);
      assert.equal(edits, 2); assert.equal(chat.queued.length, 0);
    }
    runner.unmount();
  }
});

test("failed or stopped answers pause the remaining queue without silently submitting dependent follow-ups", async () => {
  for (const stop of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null };
    let reject, calls = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body, signal) => {
      if (++calls > 1) return Promise.resolve(queueReply("재개 완료"));
      return new Promise((resolve, fail) => { reject = fail; signal.addEventListener("abort", () => fail(new Error("stopped")), { once: true }); });
    } };
    const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "첫 질문"); let chat = typeAndSend(runner, "대기 질문");
    if (stop) chat.onCancel(); else reject(new Error("offline")); chat = await renderEffects(runner);
    assert.equal(chat.queuePaused, true); assert.equal(chat.queued.length, 1); assert.equal(calls, 1);
    chat.onResumeQueue(); chat = await renderEffects(runner); assert.equal(calls, 2); assert.equal(chat.queued.length, 0); runner.unmount();
  }
});

test("queued follow-up uses the map updated by the prior answer, never the old course captured on enqueue", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(), requests = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body) => {
    requests.push(body); return requests.length === 1 ? first.promise : Promise.resolve(queueReply("후속 답변"));
  } };
  const runner = hookRunner(); let chat = await renderEffects(runner);
  chat.onContextChange({ stadium: "JAMSIL", currentCourse: { places: ["old"] } });
  chat.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 1, apply: () => null });
  typeAndSend(runner, "코스 생성"); typeAndSend(runner, "카페만 바꿔줘");
  runner.render().onContextChange({ stadium: "JAMSIL", currentCourse: { places: ["latest"] } });
  first.resolve(queueReply("새 코스")); await renderEffects(runner);
  assert.deepEqual(requests[1].context.currentCourse.places, ["latest"]); runner.unmount();
});

test("identity change drops queued requests and late completion cannot submit them for the next account", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 100 } };
  const first = deferred(); let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: () => { calls++; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "첫 계정"); typeAndSend(runner, "예약");
  global.__memberAuth = { status: "authenticated", user: { id: 200 } }; let chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 0); first.resolve(queueReply("늦은 답변")); chat = await renderEffects(runner);
  assert.equal(calls, 1); assert.equal(chat.messages.length, 0); runner.unmount();
});

test("cancelling the queue while the usage check is pending never sends the cancelled item", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(), usage = deferred(); let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatUsage: () => usage.promise,
    sendChatMessage: () => { calls++; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "현재"); typeAndSend(runner, "취소할 예약");
  first.resolve(queueReply("완료")); let chat = await renderEffects(runner); chat.onRemoveQueued(chat.queued[0].id);
  usage.resolve({ active_turn: false, can_send: true }); chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 0); assert.equal(calls, 1); runner.unmount();
});

test("a paused queue stays with its conversation and cannot leak into another room", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  let reject, calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: () => { calls++; return new Promise((resolve, fail) => { reject = fail; }); } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "기존 대화"); typeAndSend(runner, "기존 예약");
  reject(new Error("offline")); let chat = await renderEffects(runner); const room = chat.activeConversationId;
  chat.onReset(); chat = await renderEffects(runner); assert.equal(chat.queued.length, 0);
  chat.onSelectConversation(room); chat = await renderEffects(runner);
  assert.equal(chat.queued[0].content, "기존 예약"); assert.equal(chat.queuePaused, true); assert.equal(calls, 1); runner.unmount();
});

test("exhausted usage preserves queued text and pauses instead of repeatedly posting", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(); let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatUsage: async () => ({ active_turn: false, can_send: false }),
    sendChatMessage: () => { calls++; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "현재"); typeAndSend(runner, "남길 예약");
  first.resolve(queueReply("완료")); const chat = await renderEffects(runner);
  assert.equal(chat.queuePaused, true); assert.equal(chat.queued[0].content, "남길 예약"); assert.equal(calls, 1); runner.unmount();
});
