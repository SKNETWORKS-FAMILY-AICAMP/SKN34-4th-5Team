import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
const require = createRequire(import.meta.url);
const ts = require("typescript"), React = require("react");
function load(path, mocks, globals = {}) {
  const context = { ...globals, exports: {}, Error, require(id) {
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
test("members show received report totals and reset pagination when filtering", () => {
  for (const loading of [false, true]) {
    const values = [member, "", "", false, () => {}, { count: 1, results: [{ ...member, username: "member", date_joined: "2026-10-14", report_count: 7 }] }, "", loading, "", 3, 0, "reported_first"];
    let index = 0;
    const panel = load("../components/admin-panels.tsx", {
      react: { ...React, useState() { const i = index++; return [values[i], value => { values[i] = value; }]; }, useEffect() {} },
      "next/link": { __esModule: true, default: "a" },
      "@/lib/api/auth": {}, "@/lib/member-policy": { memberRoleLabel: () => "회원" }, "@/lib/team-community": {}, "@/lib/api/admin-community": {},
    }).AdminMembersPanel();
    const all = nodes(panel), filter = all.find(n => n.type === "select");
    assert.equal(filter.props.value, "reported_first");
    assert.equal(filter.props.disabled, loading);
    assert.ok(all.some(n => n.type === "th" && n.props.children === "누적 신고"));
    assert.ok(all.some(n => n.type === "span" && n.props.children === 7));
    for (const value of ["reported", "newest", "reported_first"]) {
      values[9] = 3;
      filter.props.onChange({ target: { value } });
      assert.equal(values[11], value);
      assert.equal(values[9], 1);
    }
  }
});
function postPanel({ hidden = false, reviewed = false, loading = false, busy = false, confirm = true, fail = false } = {}) {
  const calls = [];
  const post = { source_id: "free-1", post_number: "1", board: "free", title: "글", is_hidden: hidden, is_reviewed: reviewed, report_count: 0 };
  const values = ["", "", busy, { count: 1, results: [post] }, "", loading, "", 1, 0, "reported_first"];
  let index = 0;
  const panel = load("../components/admin-panels.tsx", {
    react: { ...React, useState() { const i = index++; return [values[i], value => { values[i] = typeof value === "function" ? value(values[i]) : value; }]; }, useEffect() {} },
    "next/link": { __esModule: true, default: "a" },
    "@/lib/api/auth": {}, "@/lib/member-policy": {}, "@/lib/team-community": {},
    "@/lib/api/admin-community": {
      async hideAdminPost(id) { calls.push(id); if (fail) throw new Error("숨김 실패"); },
      async setAdminPostReviewed(id, value) { calls.push([id, value]); if (fail) throw new Error("검토 상태 변경 실패"); },
    },
  }, { window: { confirm: () => confirm } }).AdminPostsPanel();
  return { values, calls, filter: nodes(panel).find(n => n.type === "select"), reviewButtons: nodes(panel).filter(n => n.type === "button" && ["검토 완료", "미완료"].includes(n.props.children)), button: nodes(panel).find(n => n.type === "button" && ["숨김", "숨김 처리됨"].includes(n.props.children)) };
}

test("posts default to reported priority and filter changes reset pagination", () => {
  const view = postPanel();
  assert.equal(view.filter.props.value, "reported_first");
  for (const filter of ["reported", "newest", "reported_first"]) {
    view.values[7] = 3;
    view.filter.props.onChange({ target: { value: filter } });
    assert.equal(view.values[9], filter);
    assert.equal(view.values[7], 1);
  }
  assert.equal(postPanel({ loading: true }).filter.props.disabled, true);
  assert.equal(postPanel({ busy: true }).filter.props.disabled, true);
});

test("one review button displays the current state, toggles it, refreshes, and preserves errors", async () => {
  for (const reviewed of [false, true]) {
    for (const fail of [false, true]) {
      const view = postPanel({ reviewed, fail, hidden: true });
      assert.equal(view.reviewButtons.length, 1);
      const active = view.reviewButtons.find(button => !button.props.disabled);
      assert.equal(active.props.children, reviewed ? "검토 완료" : "미완료");
      assert.equal(active.props["aria-pressed"], reviewed);
      active.props.onClick();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(view.calls[0][0], "free-1");
      assert.equal(view.calls[0][1], !reviewed);
      assert.equal(view.values[2], false);
      assert.equal(view.values[8], fail ? 0 : 1);
      if (fail) { assert.equal(view.values[0], ""); assert.equal(view.values[1], "검토 상태 변경 실패"); }
      else assert.match(view.values[0], /검토.*변경했어요/);
    }
  }
  for (const options of [{ busy: true }, { loading: true }]) {
    const view = postPanel(options);
    assert.ok(view.reviewButtons.every(button => button.props.disabled));
    view.reviewButtons[0].props.onClick();
    assert.equal(view.calls.length, 0);
  }
});

test("review API uses authenticated PATCH with an encoded ID and explicit target state", async () => {
  const calls = [], memberFetch = () => {};
  const api = load("../lib/api/admin-community.ts", {
    "./client": { apiRequest: (...args) => calls.push(args) },
    "../member-auth-request": { memberFetch },
  }, { URLSearchParams, encodeURIComponent });
  await api.setAdminPostReviewed("test/id", true);
  await api.setAdminPostReviewed("test/id", false);
  for (const [index, action] of ["reviewed", "unreviewed"].entries()) {
    assert.equal(calls[index][0], "/api/v1/community/admin/posts/test%2Fid/");
    assert.equal(calls[index][1].method, "PATCH");
    assert.equal(JSON.parse(calls[index][1].body).action, action);
    assert.equal(calls[index][2], memberFetch);
  }
});

test("post hide confirms, refreshes without deleting, and handles errors", async () => {
  for (const options of [{}, { confirm: false }, { fail: true }]) {
    const view = postPanel(options);
    view.button.props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(view.calls.length, options.confirm === false ? 0 : 1);
    assert.equal(view.values[2], false);
    if (options.fail) { assert.equal(view.values[1], "숨김 실패"); assert.equal(view.values[0], ""); }
    else if (options.confirm !== false) { assert.match(view.values[0], /숨겼어요/); assert.equal(view.values[8], 1); }
  }
  assert.equal(postPanel({ hidden: true }).button.props.disabled, true);
  assert.equal(postPanel({ busy: true }).button.props.disabled, true);
});
function reportPanel(status, { busy = false, hidden = false, fail = false } = {}) {
  const calls = [];
  const report = { id: 1, status, created_at: "2026-10-06T00:00:00Z", reason: "spam", reporter: "회원", post: { post_number: "1", source_id: "free-1", board: "free", title: "글", author: "작성자", is_hidden: hidden } };
  const values = ["", "", busy, null, { count: 1, results: [report] }, "", false, "", 1, 0];
  let index = 0;
  const panel = load("../components/admin-panels.tsx", {
    react: { ...React, useState() { const i = index++; return [values[i], value => { values[i] = value; }]; }, useEffect() {}, useRef: () => ({ current: null }) },
    "next/link": { __esModule: true, default: "a" },
    "@/lib/api/auth": {}, "@/lib/member-policy": {}, "@/lib/team-community": {},
    "@/lib/api/admin-community": { reportReasonLabel: {}, reportStatusLabel: {}, sanctionLabel: {}, async actOnAdminReport(...args) { calls.push(args); if (fail) throw new Error("처리 실패"); } },
  }).AdminReportsPanel();
  return { values, calls, button: nodes(panel).find(n => n.type === "button" && ["보류", "보류 취소"].includes(n.props.children)) };
}
test("held reports cancel hold; pending reports can be held", async () => {
  for (const [status, action, label] of [["pending", "hold", "보류"], ["held", "unhold", "보류 취소"]]) {
    const view = reportPanel(status);
    assert.equal(view.button.props.children, label);
    assert.equal(view.button.props.disabled, false);
    view.button.props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(view.calls[0][1], action);
    assert.ok(view.values[0].includes(action === "unhold" ? "처리 대기" : "보류"));
  }
});
test("hold changes are disabled for hidden reports and pending requests", () => {
  for (const [status, options] of [["hidden", {}], ["held", { hidden: true }], ["held", { busy: true }]]) {
    assert.equal(reportPanel(status, options).button.props.disabled, true);
  }
});
test("failed hold cancellation shows an error, not a success notice", async () => {
  const view = reportPanel("held", { fail: true });
  view.button.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(view.values[0], "");
  assert.equal(view.values[1], "처리 실패");
});
const panels = { AdminMembersPanel: "members-panel", AdminPostsPanel: "posts-panel", AdminReportsPanel: "reports-panel", ModerationPanel: "moderation-panel" };
function dashboard(identity, tab = "members", reload = () => {}) {
  return load("../app/admin/page.tsx", {
    "next/link": { __esModule: true, default: "a" },
    "next/navigation": { useSearchParams: () => new URLSearchParams({ tab }) },
    "@/lib/member-auth": { useMemberAuth: () => ({ ...identity, reload }) },
    "@/components/admin-panels": panels,
    "@/components/community-moderation": { ModerationPanel: panels.ModerationPanel },
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
  for (const [tab, panel] of [["moderation", "moderation-panel"], ["members", "members-panel"], ["posts", "posts-panel"], ["reports", "reports-panel"], ["invalid", "members-panel"]]) {
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

test("feedback entry shares the primary menu styling and is exclusive to superusers", () => {
  for (const is_superuser of [false, true]) {
    const tree = nodes(dashboard({ status: "authenticated", user: { ...member, is_superuser } }));
    const menu = tree.find(n => n.type === "nav" && n.props["aria-label"] === "관리 메뉴");
    const links = nodes(menu).filter(n => n.props.href);
    assert.equal(links.length, is_superuser ? 5 : 4);
    const feedback = tree.filter(n => n.props.href === "/admin/feedback");
    assert.equal(feedback.length, is_superuser ? 1 : 0);
    if (is_superuser) {
      assert.equal(links.at(-1), feedback[0]);
      assert.equal(feedback[0].props.className, links[0].props.className);
    }
  }
});
test("category menu ends with Admin only for authenticated active staff; header has no duplicate", () => {
  for (const [status, user, allowed] of [["authenticated", member, true], ["anonymous", null, false], ["loading", member, false], ["unavailable", member, false], ["authenticated", { ...member, is_staff: false }, false], ["authenticated", { ...member, is_active: false }, false]]) {
    const header = load("../components/member-header-actions.tsx", {
      react: { ...React, useCallback: callback => callback, useState: value => [value, () => {}], useRef: () => ({ current: null }), useEffect() {} },
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
