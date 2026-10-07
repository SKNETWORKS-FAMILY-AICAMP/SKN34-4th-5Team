import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
const require = createRequire(import.meta.url);
const ts = require("typescript"), React = require("react");
function load(path, mocks) {
  const context = { exports: {}, require(id) {
    if (id in mocks) return mocks[id];
    if (id === "react" || id === "react/jsx-runtime") return require(id);
    if (id.endsWith(".css")) return {};
    throw new Error(id);
  } };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, context);
  return context.exports;
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!React.isValidElement(tree)) return [];
  return [tree, ...nodes(tree.props.children)];
}
const member = { id: 18, is_active: true, is_staff: true, is_superuser: false };
const panels = { AdminMembersPanel: "members-panel", AdminPostsPanel: "posts-panel", AdminReportsPanel: "reports-panel" };
function dashboard(identity, tab = "members", reload = () => {}) {
  return load("../app/admin/page.tsx", {
    "next/link": { __esModule: true, default: "a" },
    "next/navigation": { useSearchParams: () => new URLSearchParams({ tab }) },
    "@/lib/member-auth": { useMemberAuth: () => ({ ...identity, reload }) },
    "@/components/admin-panels": panels,
  }).AdminDashboard();
}
test("dashboard blocks panels while unauthenticated, unavailable, loading or unauthorized", () => {
  for (const identity of [
    { status: "anonymous", user: null }, { status: "loading", user: member },
    { status: "unavailable", user: member },
    { status: "authenticated", user: { ...member, is_staff: false } },
    { status: "authenticated", user: { ...member, is_active: false } },
  ]) assert.equal(nodes(dashboard(identity)).some(n => Object.values(panels).includes(n.type)), false);
});
test("each menu mounts only its panel; invalid tabs default to members", () => {
  for (const [tab, panel] of [["members", "members-panel"], ["posts", "posts-panel"], ["reports", "reports-panel"], ["invalid", "members-panel"]]) {
    const tree = nodes(dashboard({ status: "authenticated", user: member }, tab));
    assert.deepEqual(tree.filter(n => Object.values(panels).includes(n.type)).map(n => n.type), [panel]);
    assert.equal(tree.filter(n => n.props["aria-current"] === "page").length, 1);
    assert.equal(tree.some(n => n.props.href === "/admin/feedback"), false);
  }
  assert.ok(nodes(dashboard({ status: "authenticated", user: { ...member, is_superuser: true } })).some(n => n.props.href === "/admin/feedback"));
});
test("auth failure has a working retry and guests have login entry", () => {
  let calls = 0;
  nodes(dashboard({ status: "unavailable" }, "members", () => { calls++; })).find(n => n.type === "button").props.onClick();
  assert.equal(calls, 1);
  assert.ok(nodes(dashboard({ status: "anonymous" })).some(n => n.props.href === "/login?next=admin"));
});
test("category menu ends with Admin only for authenticated active staff; header has no duplicate", () => {
  for (const [status, user, allowed] of [["authenticated", member, true], ["anonymous", null, false], ["loading", member, false], ["unavailable", member, false], ["authenticated", { ...member, is_staff: false }, false], ["authenticated", { ...member, is_active: false }, false]]) {
    const header = load("../components/member-header-actions.tsx", {
      react: { ...React, useState: value => [value, () => {}], useRef: () => ({ current: null }), useEffect() {} },
      "next/link": { __esModule: true, default: "a" }, "next/navigation": { useRouter: () => ({}) },
      "@/lib/member-auth": { useMemberAuth: () => ({ status, user }) }, "@/lib/member-auth-request": {},
    });
    assert.equal(nodes(header.MemberHeaderActions()).some(n => n.props.href === "/admin"), false);
    let closed = false;
    const menu = load("../components/header-menu.tsx", {
      react: { ...React, useState: () => [true, value => { closed = value === false; }], useRef: () => ({ current: null }), useEffect() {}, useId: () => "test-menu" },
      "next/link": { __esModule: true, default: "a" },
      "next/image": { __esModule: true, default: "img" },
      "next/navigation": { usePathname: () => "/admin" },
      "@/lib/member-auth": { useMemberAuth: () => ({ status, user }) },
      "./chat-provider": { useChat: () => ({ onExpand() {} }) },
      "./icons": { Icon: "icon", CapBot: "cap-bot" },
    }).HeaderMenu();
    const tree = nodes(menu.type(menu.props));
    const admin = tree.find(n => n.props.href === "/admin");
    assert.equal(Boolean(admin), allowed);
    if (allowed) {
      const actions = tree.filter(n => n.type === "a" || n.type === "button");
      assert.equal(actions.at(-1), admin);
      assert.equal(admin.props["aria-current"], "page");
      admin.props.onClick();
      assert.equal(closed, true);
    }
  }
});
