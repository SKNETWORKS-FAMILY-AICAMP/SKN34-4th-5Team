import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const jsx = (type, props) => ({ type, props });
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  return tree && typeof tree === "object" && tree.props ? [tree, ...nodes(tree.props.children)] : [];
}
function harness({ user = { id: 1 }, response = Response.json({}) } = {}) {
  let values = [], index = 0;
  const calls = [];
  const mocks = {
    react: { useEffect() {}, useState(initial) { const i = index++; if (!(i in values)) values[i] = initial; return [values[i], value => { values[i] = typeof value === "function" ? value(values[i]) : value; }]; } },
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "next/link": { __esModule: true, default: "a" },
    "@/lib/member-auth": { useMemberAuth: () => ({ user, status: user ? "authenticated" : "anonymous" }) },
    "@/lib/member-auth-request": { memberFetch: async (path, init) => { calls.push({ path, init }); return response; }, memberError: (data, fallback) => data.detail || fallback },
    "@/lib/community-api": {},
    "@/lib/team-community": { getTeamBoardHref: () => "/community/teams" },
  };
  const context = { exports: {}, require: name => mocks[name], Error, JSON, window: { confirm: () => true } };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../components/community-moderation.tsx", import.meta.url), "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, context);
  return { exports: context.exports, calls, render(fn, props, state = []) { values = state; index = 0; return fn(props); }, get values() { return values; } };
}
const job = { id: 9, postId: "p1", commentId: null, revision: 4, review_revision: 2, state: "done", manualHidden: false,
  payload: { text: "본문", mode: "manual", image_ids: [] }, findings: [{ id: "f1", code: "political", resolution: "open", reason: "확인", quote: "본문" }], error_code: "" };
function card(h, selected = ["f1"], note = "원문 확인") {
  const panel = h.render(h.exports.ModerationPanel, {}, [[job], 1, false, 0, ""]);
  const element = nodes(panel).find(node => typeof node.type === "function");
  return h.render(element.type, element.props, [selected, note, false, ""]);
}

test("review sends selected reasons and both revisions using authenticated API", async () => {
  const h = harness();
  const tree = card(h);
  const restore = nodes(tree).find(node => node.type === "button" && node.props.children === "선택 사유 해제·복구 검토");
  assert.equal(restore.props.disabled, false);
  restore.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls[0].path, "/api/v1/community/admin/moderation/9/review/");
  assert.deepEqual(JSON.parse(h.calls[0].init.body), { revision: 4, review_revision: 2, finding_ids: ["f1"], action: "dismiss", note: "원문 확인" });
  assert.equal(h.calls[0].init.cache, "no-store");
});

test("empty reason selection or explanation disables review", () => {
  for (const [selected, note] of [[[], "확인"], [["f1"], " "]]) {
    const h = harness();
    const buttons = nodes(card(h, selected, note)).filter(node => node.type === "button");
    assert.ok(buttons.every(node => node.props.disabled));
  }
});

test("conflicting review displays error and never retries approval automatically", async () => {
  const h = harness({ response: Response.json({ detail: "내용이 변경되었습니다." }, { status: 409 }) });
  nodes(card(h)).find(node => node.type === "button").props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 1);
  assert.equal(h.values[3], "내용이 변경되었습니다.");
});

test("anonymous submission screen cannot render private content", () => {
  const h = harness({ user: null });
  const tree = h.render(h.exports.CommunitySubmissions);
  assert.ok(nodes(tree).some(node => node.props.href === "/login"));
  assert.equal(nodes(tree).some(node => typeof node.type === "function"), false);
});
