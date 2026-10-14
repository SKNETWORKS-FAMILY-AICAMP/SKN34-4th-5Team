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
function harness({ user = { id: 1 }, response = Response.json({}), confirm = true, deleteError = null } = {}) {
  let values = [], index = 0;
  const calls = [];
  const mocks = {
    react: { useEffect() {}, useState(initial) { const i = index++; if (!(i in values)) values[i] = initial; return [values[i], value => { values[i] = typeof value === "function" ? value(values[i]) : value; }]; } },
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "next/link": { __esModule: true, default: "a" },
    "@/lib/member-auth": { useMemberAuth: () => ({ user, status: user ? "authenticated" : "anonymous" }) },
    "@/lib/member-auth-request": { memberFetch: async (path, init) => { calls.push({ path, init }); return response; }, memberError: (data, fallback) => data.detail || fallback },
    "@/lib/community-api": { deleteCommunityPost: async id => { calls.push({ deleted: id }); if (deleteError) throw new Error(deleteError); } },
    "@/lib/team-community": { getTeamBoardHref: () => "/community/teams" },
    "./community-moderation.module.css": { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) },
  };
  const context = { exports: {}, require: name => mocks[name], Error, JSON, window: { confirm: () => confirm } };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../components/community-moderation.tsx", import.meta.url), "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, context);
  return { exports: context.exports, calls, render(fn, props, state = []) { values = state; index = 0; return fn(props); }, get values() { return values; } };
}
const job = { id: 9, postId: "p1", commentId: null, revision: 4, review_revision: 2, state: "done", manualHidden: false,
  payload: { text: "본문", mode: "manual", image_ids: [] }, findings: [{ id: "f1", code: "political", resolution: "open", reason: "확인", quote: "본문" }], error_code: "" };
function card(h, selected = ["f1"], note = "원문 확인") {
  const panel = h.render(h.exports.ModerationPanel, {}, [[job], 1, false, 0, "", false]);
  const element = nodes(panel).find(node => typeof node.type === "function" && node.props.job);
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

function submissionContent(h, state) {
  const screen = h.render(h.exports.CommunitySubmissions);
  const element = nodes(screen).find(node => typeof node.type === "function");
  return h.render(element.type, element.props, state);
}

test("member submissions distinguish loading, empty posts/comments and request errors", () => {
  const h = harness();
  const loading = submissionContent(h, [[], [], "", 0, false, true]);
  assert.ok(nodes(loading).some(n => n.props.role === "status" && /불러오고/.test(n.props.children)));
  assert.equal(nodes(loading).some(n => /등록한.*없어요/.test(n.props.children)), false);
  assert.equal(nodes(loading).find(n => n.type === "button").props.disabled, true);
  const refreshing = submissionContent(h, [[], [{ id: 3, revision: 1, content: "댓글", moderationStatus: "pending" }], "", 0, false, true]);
  assert.ok(nodes(refreshing).some(n => typeof n.type === "function" && n.props.comment?.id === 3), "refresh keeps the comment editor mounted and preserves its draft");
  assert.equal(nodes(refreshing).some(n => /등록한.*없어요/.test(n.props.children)), false);
  const empty = submissionContent(h, [[], [], "", 0, false, false]);
  assert.deepEqual(nodes(empty).filter(n => n.type === "h2").map(n => n.props.children), ["내 게시글 검사", "내 댓글 검사"]);
  assert.ok(nodes(empty).some(n => n.props.children === "등록한 게시글이 없어요"));
  assert.ok(nodes(empty).some(n => n.props.children === "등록한 댓글이 없어요"));
  const failed = submissionContent(h, [[], [], "연결 실패", 0, false, false]);
  assert.ok(nodes(failed).some(n => n.props.role === "alert" && n.props.children === "연결 실패"));
  assert.equal(nodes(failed).some(n => /등록한.*없어요/.test(n.props.children)), false);
});

test("member cards use padded headings, wrapping status and distinct edit/delete actions", () => {
  const h = harness();
  const tree = submission(h);
  assert.ok(nodes(tree).some(n => n.type === "article" && n.props.className === "submissionCard"));
  assert.ok(nodes(tree).some(n => n.type === "h3" && n.props.children === "비공개"));
  assert.ok(nodes(tree).some(n => n.props.className === "submissionStatus" && n.props["data-state"] === "pending"));
  assert.ok(nodes(tree).some(n => n.props.href === "/community/write?edit=private-1" && /button-primary/.test(n.props.className)));
  assert.ok(nodes(tree).some(n => n.type === "button" && n.props.children === "삭제" && /deleteButton/.test(n.props.className)));
  assert.equal(nodes(tree).some(n => n.props.children === "게시판에서 확인"), false);
  const commentTree = submissionContent(h, [[], [{ id: 3, revision: 1, content: "댓글", moderationStatus: "pending" }], "", 0, false, false]);
  const comment = nodes(commentTree).find(n => typeof n.type === "function" && n.props.comment);
  const cardTree = h.render(comment.type, comment.props, ["댓글", false, ""]);
  assert.ok(nodes(cardTree).some(n => n.type === "textarea" && n.props["aria-label"] === "댓글 3 수정" && n.props.className === "commentInput"));
  assert.ok(nodes(cardTree).some(n => n.type === "button" && n.props.children === "삭제" && /deleteButton/.test(n.props.className)));
  const css = readFileSync(new URL("../components/community-moderation.module.css", import.meta.url), "utf8");
  assert.match(css, /\.submissionCard\s*\{[^}]*padding: 24px/);
  assert.match(css, /\.submissionActions\s*\{[^}]*flex-wrap: wrap/);
  assert.match(css, /\.submissions :is\(a, button, textarea\):focus-visible/);
  assert.doesNotMatch(css.match(/\.submissionStatus\s*\{[^}]*\}/)[0], /white-space: nowrap/);
});

test("unavailable private post notice has padding and actionable styled navigation", () => {
  const source = readFileSync(new URL("../components/community-board.tsx", import.meta.url), "utf8");
  assert.match(source, /detail\.error && <div className=\{styles\.postNotice\}/);
  assert.match(source, /className="button button-secondary" href=\{listHref\}>목록으로/);
  assert.match(source, /user && <Link className="button button-primary" href="\/community\/submissions"/);
  const css = readFileSync(new URL("../components/community-board.module.css", import.meta.url), "utf8");
  assert.match(css, /\.postNotice\s*\{[^}]*padding: 24px/);
  assert.match(css, /\.noticeActions\s*\{[^}]*flex-wrap: wrap/);
});

function submission(h, status = "pending", busy = false) {
  const screen = h.render(h.exports.CommunitySubmissions);
  const element = nodes(screen).find(node => typeof node.type === "function");
  return h.render(element.type, element.props, [[{ id: "private-1", title: "비공개", content: "본문", moderationStatus: status, revision: 1 }], [], "", 0, busy, false]);
}

test("cancelled private post deletion makes zero API calls", async () => {
  const h = harness({ confirm: false });
  nodes(submission(h)).find(node => node.type === "button" && node.props.children === "삭제").props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 0);
  assert.equal(h.values[3], 0);
});

test("successful private post deletion refreshes all pending and held states", async () => {
  for (const status of ["pending", "review", "error", "rejected"]) {
    const h = harness();
    nodes(submission(h, status)).find(node => node.type === "button" && node.props.children === "삭제").props.onClick();
    assert.equal(h.values[4], true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.calls[0].deleted, "private-1");
    assert.equal(h.values[3], 1);
    assert.equal(h.values[4], false);
  }
  const h = harness();
  assert.equal(nodes(submission(h, "pending", true)).find(node => node.type === "button" && node.props.children === "삭제").props.disabled, true);
});

test("failed private post deletion preserves list and displays error", async () => {
  const h = harness({ deleteError: "삭제 실패" });
  nodes(submission(h)).find(node => node.type === "button" && node.props.children === "삭제").props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.values[0][0].id, "private-1");
  assert.equal(h.values[2], "삭제 실패");
  assert.equal(h.values[3], 0);
  assert.equal(h.values[4], false);
});

test("anonymous submission screen cannot render private content", () => {
  const h = harness({ user: null });
  const tree = h.render(h.exports.CommunitySubmissions);
  assert.ok(nodes(tree).some(node => node.props.href === "/login"));
  assert.equal(nodes(tree).some(node => typeof node.type === "function"), false);
});

test("loading hides stale review forms and prevents pagination", () => {
  const h = harness();
  const tree = h.render(h.exports.ModerationPanel, {}, [[job], 2, true, 0, "", true]);
  assert.ok(nodes(tree).some(node => node.props.role === "status"));
  assert.equal(nodes(tree).some(node => typeof node.type === "function" && node.props.job), false);
  assert.ok(nodes(tree).filter(node => node.type === "button").every(node => node.props.disabled));
});

test("mode selection saves explicit choice and reports persistence", async () => {
  const h = harness({ response: Response.json({ mode: "openai", automaticAvailable: true }) });
  let tree = h.render(h.exports.ModerationModeControl, {}, ["manual", "manual", true, false, "", ""]);
  nodes(tree).find(n => n.type === "select").props.onChange({ target: { value: "openai" } });
  tree = h.render(h.exports.ModerationModeControl, {}, h.values);
  const save = nodes(tree).find(n => n.type === "button" && n.props.children === "방식 저장");
  assert.equal(save.props.disabled, false);
  save.props.onClick();
  assert.equal(h.values[3], true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls[0].path, "/api/v1/community/admin/moderation/settings/");
  assert.deepEqual(JSON.parse(h.calls[0].init.body), { mode: "openai" });
  assert.equal(h.values[1], "openai");
  assert.match(h.values[5], /새 게시글/);
});

test("failed mode save preserves current mode and shows error", async () => {
  const h = harness({ response: Response.json({ detail: "저장 실패" }, { status: 500 }) });
  const tree = h.render(h.exports.ModerationModeControl, {}, ["openai", "manual", true, false, "", ""]);
  nodes(tree).find(n => n.type === "button" && n.props.children === "방식 저장").props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.values[1], "manual");
  assert.equal(h.values[4], "저장 실패");
  assert.equal(h.values[3], false);
});

test("mode save is disabled before load or with unchanged choice", () => {
  const h = harness();
  for (const state of [["manual", "", false, true, "", ""], ["manual", "manual", false, false, "", ""]]) {
    const tree = h.render(h.exports.ModerationModeControl, {}, state);
    assert.equal(nodes(tree).find(n => n.type === "button" && n.props.children === "방식 저장").props.disabled, true);
    assert.equal(nodes(tree).find(n => n.type === "option" && n.props.value === "openai").props.disabled, true);
  }
});

test("public fallback instructions keep manual review distinct from provider approval", () => {
  const h = harness();
  assert.match(h.exports.moderationLabel.review, /운영진 검토/);
  assert.doesNotMatch(h.exports.moderationLabel.review, /검사 승인|비공개/);
  const mode = h.render(h.exports.ModerationModeControl, {}, ["openai", "openai", true, false, "", ""]);
  assert.ok(nodes(mode).some(node => typeof node.props.children === "string" && node.props.children.includes("공개와 운영진 검토를 병행")));
  assert.ok(nodes(mode).some(node => typeof node.props.children === "string" && node.props.children.includes("비공개 부모 글의 댓글은 비공개로 유지")));
  const tree = card(h);
  assert.ok(nodes(tree).some(node => node.props.children === "선택 사유 확정·비공개 처리"));
  assert.ok(nodes(tree).some(node => typeof node.props.children === "string" && node.props.children.includes("다른 미해제 사유나 확정 사유가 있으면 비공개")));
});

test("empty results explain the next action without masking request errors", () => {
  const h = harness();
  const empty = h.render(h.exports.ModerationPanel, {}, [[], 1, false, 0, "", false]);
  assert.ok(nodes(empty).some(node => node.props.children === "표시할 검사 내역이 없습니다"));
  const failed = h.render(h.exports.ModerationPanel, {}, [[], 1, false, 0, "연결 실패", false]);
  assert.ok(nodes(failed).some(node => node.props.role === "alert" && node.props.children === "연결 실패"));
  assert.equal(nodes(failed).some(node => node.props.children === "표시할 검사 내역이 없습니다"), false);
});
