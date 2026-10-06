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

for (const name of ["lib/chat/types", "lib/chat/validation", "lib/chat/progress", "lib/chat/history"]) compile(name);
mkdirSync(join(scratch, "components"), { recursive: true });

const providerSource = readFileSync(join(frontend, "components/chat-provider.tsx"), "utf8")
  .replace('from "react"', 'from "../test-react"')
  .replace('from "next/navigation"', 'from "../test-navigation"')
  .replaceAll('from "@/lib/chat/types"', 'from "../lib/chat/types"')
  .replace('from "@/lib/chat/client"', 'from "../test-chat-client"')
  .replace('from "@/lib/chat/progress"', 'from "../lib/chat/progress"')
  .replace('from "@/lib/chat/history"', 'from "../lib/chat/history"')
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
exports.usePathname = () => "/";
exports.useRouter = () => ({ push() {} });
`);
writeFileSync(join(scratch, "test-member-auth.js"), `exports.useMemberAuth = () => global.__memberAuth;`);
writeFileSync(join(scratch, "test-client-id.js"), `exports.createClientId = () => "new-chat";`);
writeFileSync(join(scratch, "test-popup.js"), `exports.ChatPopup = () => null;`);
writeFileSync(join(scratch, "test-chat-client.js"), `
class ChatClientError extends Error {}
exports.ChatClientError = ChatClientError;
exports.GUEST_STATUS = { provider: "guest", model: "guest", ready: true };
for (const name of ["fetchChatHistory", "fetchChatTurns", "getChatStatus", "listChatSessions", "sendChatMessage", "sendGuestChatMessage"])
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
const rooms = [{ id: 1, title: "첫 대화" }, { id: 2, title: "둘째 대화" }];
const progress = [{
  turn_id: "11111111-1111-4111-8111-111111111111", sequence_no: 1,
  operation_id: "22222222-2222-4222-8222-222222222222", parent_operation_id: null,
  kind: "tool", status: "completed", label: "조회 완료", created_at: "2026-09-16T03:00:00Z",
  tool_name: "get_games", summary: null,
}];

global.window = {
  location: { pathname: "/", search: "", hash: "", origin: "http://localhost" }, scrollY: 0,
  scrollTo() {}, matchMedia: () => ({ matches: false }),
};
global.requestAnimationFrame = callback => { callback(); return 1; };
global.cancelAnimationFrame = () => {};
global.__memberAuth = { status: "authenticated", user: { id: 7 } };

test("provider ignores delayed list/history callbacks and reloads an interrupted room", async () => {
  const lateList = deferred();
  global.__chatApi = {
    listChatSessions: () => lateList.promise,
    fetchChatHistory: async () => [], fetchChatTurns: async () => [],
    getChatStatus: async () => ({ provider: "member", model: "server", ready: true }),
    sendChatMessage: async () => { throw new Error("not used"); },
    sendGuestChatMessage: async () => { throw new Error("not used"); },
  };
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

  const firstHistory = deferred(), firstTurns = deferred(), secondHistory = deferred(), secondTurns = deferred();
  const historyCalls = [];
  global.__chatApi = {
    ...global.__chatApi,
    listChatSessions: async () => rooms,
    fetchChatHistory: sessionId => {
      historyCalls.push(sessionId);
      if (sessionId === 1 && historyCalls.filter(id => id === 1).length === 1) return firstHistory.promise;
      if (sessionId === 2) return secondHistory.promise;
      return Promise.resolve([]);
    },
    fetchChatTurns: sessionId => sessionId === 1 ? firstTurns.promise : secondTurns.promise,
  };
  runner = hookRunner();
  controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, "member:1");

  controls.onSelectConversation("member:2");
  controls = runner.render();
  controls.onDraftChange("둘째 방 초안");
  firstHistory.resolve([{ id: 10, sequence_no: 1, role: "human", content: "늦은 첫 기록", status: "completed" }]);
  firstTurns.resolve([]);
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, "member:2");
  assert.equal(controls.draft, "둘째 방 초안");

  secondHistory.resolve([
    { id: 21, sequence_no: 1, role: "human", content: "둘째 질문", status: "completed" },
    { id: 22, sequence_no: 2, role: "ai", content: "둘째 답변", status: "completed" },
  ]);
  secondTurns.resolve([{ id: "11111111-1111-4111-8111-111111111111", question: "둘째 질문", status: "completed", base_sequence: 0, human_message_id: 21, assistant_message_id: 22, progress }]);
  await tick();
  controls = runner.render();
  assert.equal(controls.messages.at(-1).content, "둘째 답변");
  assert.equal(controls.messages.at(-1).progress[0].label, "조회 완료");
  assert.equal(controls.draft, "둘째 방 초안");

  controls.onSelectConversation("member:1");
  assert.deepEqual(historyCalls, [1, 2, 1]);
});
