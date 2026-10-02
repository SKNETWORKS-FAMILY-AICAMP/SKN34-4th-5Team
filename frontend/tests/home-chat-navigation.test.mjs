import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const React = require("react");

function load(path, mocks) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const context = { exports: {}, require(id) {
    if (id in mocks) return mocks[id];
    if (id === "react" || id === "react/jsx-runtime") return require(id);
    if (id.endsWith(".css")) return {};
    throw new Error(`Unexpected dependency: ${id}`);
  } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, context);
  return context.exports;
}
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!React.isValidElement(node)) return [];
  return [node, ...elements(node.props.children)];
}

test("anonymous home CTA navigates to the rendered chat page without submitting a question", () => {
  let status = "anonymous";
  const calls = [];
  const auth = { useMemberAuth: () => ({ status, user: null }) };
  const chat = { messages: [], conversations: [], draft: "", pending: "", failed: "",
    activeConversationId: "initial-chat", status: null, statusLoading: false,
    editingMessageId: null, timeline: [], openChat: (...args) => calls.push(args) };
  const hooks = { ...React, useState: value => [value, () => {}],
    useRef: value => ({ current: value }), useEffect() {} };
  const common = { react: hooks, "next/link": { __esModule: true, default: "a" },
    "next/image": { __esModule: true, default: "img" },
    "@/lib/member-auth": auth, "./chat-provider": { useChat: () => chat },
    "@/lib/chat/types": { MAX_MESSAGE_LENGTH: 2000 },
    "./icons": { Icon: "icon", Baseball: "baseball", CapBot: "cap-bot" } };
  const home = load("../components/home-page.tsx", { ...common,
    "@/lib/routes": { useRoutes: () => [], useRoutesReady: () => true },
    "./game-schedule": { GameSchedule: "schedule" }, "./ad-slot": { AdSlot: "ad" },
    "./route-card": { RouteCard: "route" }, "./route-skeleton": { RouteCardsSkeleton: "skeleton" } });
  const hero = elements(home.HomePage()).find(item => item.props.className === "hero-search");
  assert.equal(hero.type, "a");
  assert.equal(hero.props.href, "/chat");
  assert.equal(hero.props.onClick, undefined);
  assert.ok(!elements(hero).some(item => item.type === "form"));
  assert.deepEqual(calls, []);

  const workspace = load("../components/chat-workspace.tsx", { ...common,
    "./chat-answer": { ChatAnswer: "answer" }, "./chat-planning": { ChatQuestions: "questions", ChatUserContent: "content", ChatWriterOffer: "offer" },
    "./chat-feedback": { ChatFeedback: "feedback" }, "./chat-course-card": { ChatCourseCard: "course" },
    "./chat-pending": { ChatPending: "pending" }, "./chat-progress": { ChatProgress: "progress", ChatSubAgentStatus: "subagents" },
    "./chat-usage": { ChatUsage: "usage" } });
  const destination = load("../app/chat/page.tsx", { "@/components/chat-workspace": workspace });
  assert.equal(destination.default().type, workspace.ChatWorkspace);
  const rendered = workspace.ChatWorkspace();
  assert.equal(rendered.props.className, "chat-workspace");
  assert.ok(elements(rendered).some(item => item.type === "h1" && item.props.children === "직관 도우미"));
  assert.deepEqual(calls, []);

  status = "authenticated";
  const memberHero = elements(home.HomePage()).find(item => item.props.className === "hero-search");
  assert.equal(memberHero.type, "form");
  memberHero.props.onSubmit({ preventDefault() {} });
  assert.deepEqual(calls, [[""]]);
});
