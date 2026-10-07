import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const noop = () => null;
function load(path, dependencies) {
  const ast = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const body = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join("\n");
  const testModule = { exports: {} };
  new Function("require", "module", "exports", ...Object.keys(dependencies), ts.transpileModule(body, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText)(require, testModule, testModule.exports, ...Object.values(dependencies));
  return testModule.exports;
}
function harness() {
  const states = [], effects = [];
  let cursor = 0, pending = [];
  return {
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      return states[index] ??= { current: initial };
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!effects[index] || !dependencies.every((value, key) => Object.is(value, effects[index].dependencies[key]))) {
        const old = effects[index];
        effects[index] = { dependencies };
        pending.push(() => { old?.cleanup?.(); effects[index].cleanup = effect(); });
      }
    },
    render(Component) { cursor = 0; return Component(); },
    flush() { const next = pending; pending = []; next.forEach(run => run()); },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
}
function nodes(tree) {
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}
const find = (tree, predicate) => nodes(tree).find(predicate);
const text = tree => JSON.stringify(tree);
const styles = new Proxy({}, { get: (_, key) => key });
const roles = [
  { status: "anonymous", user: null },
  { status: "authenticated", user: { id: 1, is_staff: false, is_superuser: false } },
  { status: "authenticated", user: { id: 2, is_staff: true, is_superuser: false } },
  { status: "authenticated", user: { id: 3, is_staff: false, is_superuser: true } },
  { status: "loading", user: null },
];

test("account menu retains feedback for superusers but removes community administration", () => {
  for (const identity of roles) {
    const hooks = harness();
    const { MemberHeaderActions } = load("components/member-header-actions.tsx", {
      ...hooks, Link: noop, useMemberAuth: () => identity, useRouter: () => ({}), logoutMember: noop, memberError: noop,
    });
    let tree = hooks.render(MemberHeaderActions);
    find(tree, node => node.props?.["aria-controls"] === "member-menu-panel")?.props.onClick();
    tree = hooks.render(MemberHeaderActions);
    const links = nodes(tree).filter(node => node.props?.href).map(node => node.props.href);
    const allowed = identity.user?.is_superuser === true;
    assert.equal(links.includes("/mypage?tab=feedback"), allowed);
    if (allowed) {
      const index = links.indexOf("/mypage?tab=feedback");
      assert.equal(links.includes("/mypage?tab=reports"), false);
      assert.equal(links[index + 1], "/mypage?tab=profile");
      assert.equal(find(tree, node => node.props?.href === "/mypage?tab=feedback").props.children, "챗봇 답변 평가");
    }
  }
});

test("mypage validates feedback deep links and mounts the shared panel only for superusers", () => {
  for (const identity of roles) {
    const hooks = harness(), pushes = [];
    const dependencies = {
      ...hooks, Link: noop, Image: noop, Suspense: noop, useMemberAuth: () => identity,
      useRouter: () => ({ push: (...args) => pushes.push(args) }), useSearchParams: () => new URLSearchParams("tab=feedback"),
      useRoutesReady: () => true, useRoutes: () => [], useRoutesError: () => null, useLikedRoutes: () => [],
      teamBoards: [], memberRoleLabel: () => "회원", nextNicknameChangeAt: () => null, styles,
      AdminFeedbackPanel: noop, AdminMembersPanel: noop, AdminPostsPanel: noop, AdminReportsPanel: noop,
      MemberPosts: noop, MemberAccountSettings: noop, NicknameChangeButton: noop, PasswordChangeButton: noop,
      ProfilePhotoEditor: noop, RouteCard: noop, logoutMember: noop, memberError: noop,
    };
    const { default: MyPage } = load("app/mypage/page.tsx", dependencies);
    const Content = MyPage().props.children.type;
    const tree = hooks.render(Content);
    const allowed = identity.user?.is_superuser === true;
    assert.equal(Boolean(find(tree, node => node.key === String(identity.user?.id))), allowed);
    const tab = find(tree, node => node.type === "button" && node.props.children === "챗봇 답변 평가");
    assert.equal(Boolean(tab), allowed);
    if (allowed) {
      assert.equal(tab.props["aria-pressed"], true);
      tab.props.onClick();
      assert.deepEqual(pushes, [["/mypage?tab=feedback", { scroll: false }]]);
      const labels = nodes(tree).filter(node => node.type === "button").map(node => node.props.children);
      assert.equal(labels.includes("신고 관리"), false);
      assert.equal(labels[labels.indexOf("챗봇 답변 평가") + 1], "회원 정보");
    } else assert.doesNotMatch(text(tree), /평가 상세|질문 스냅샷/);
  }
});

function panel(identity, fetchList, fetchDetail = async () => ({})) {
  const hooks = harness();
  const { AdminFeedbackPanel } = load("components/admin-feedback-panel.tsx", {
    ...hooks, useMemberAuth: () => identity, FEEDBACK_REASONS: { inaccurate: "부정확해요" },
    fetchAdminFeedback: fetchList, fetchAdminFeedbackDetail: fetchDetail, styles, panelStyles: styles,
  });
  return { ...hooks, render: () => hooks.render(AdminFeedbackPanel) };
}

test("shared panel blocks both privileged requests and records for logged-out, regular, staff and loading states", () => {
  for (const identity of roles.filter(role => !role.user?.is_superuser)) {
    let calls = 0;
    const view = panel(identity, () => { calls++; }, () => { calls++; });
    const tree = view.render(); view.flush();
    assert.equal(calls, 0);
    assert.equal(find(tree, node => node.type === "select"), undefined);
    assert.doesNotMatch(text(tree), /질문 스냅샷|평가 상세/);
    view.unmount();
  }
});

test("shared panel retains list, detail snapshots, filters, pagination, refresh and loading", async () => {
  const calls = [], details = [];
  const row = { id: 7, rating: "down", question: "질문 내용", answer: "답변 내용", updated_at: "2026-10-01", reason: "inaccurate", metadata: { actual: true } };
  const view = panel(roles[3], async (...args) => { calls.push(args); return { count: 21, results: [row] }; }, async (id, signal) => { details.push({ id, signal }); return row; });
  view.render(); view.flush(); await Promise.resolve();
  assert.match(text(view.render()), /평가 불러오는 중/);
  await tick();
  let tree = view.render();
  assert.match(text(tree), /21건 · 1페이지/);
  find(tree, node => node.type === "button" && node.props["aria-pressed"] === false).props.onClick();
  view.render(); view.flush(); await tick(); tree = view.render();
  assert.equal(details[0].id, 7);
  assert.match(text(tree), /질문 스냅샷.*답변 스냅샷.*실제 저장 메타데이터/);
  find(tree, node => node.props?.children === "상세 닫기").props.onClick();
  assert.equal(find(view.render(), node => node.props?.["aria-label"] === "평가 상세"), undefined);
  find(tree, node => node.props?.children === "다음").props.onClick();
  view.render(); view.flush(); await tick();
  assert.equal(calls.at(-1)[0], 2);
  tree = view.render();
  const filters = nodes(tree).filter(node => node.type === "select");
  filters[0].props.onChange({ target: { value: "down" } });
  filters[1].props.onChange({ target: { value: "inaccurate" } });
  view.render(); view.flush(); await tick();
  assert.deepEqual(calls.at(-1).slice(0, 3), [1, "down", "inaccurate"]);
  const count = calls.length;
  find(view.render(), node => node.props?.children === "새로고침").props.onClick();
  view.render(); view.flush(); await tick();
  assert.equal(calls.length, count + 1);
  view.unmount();
  assert.equal(calls.at(-1)[3].aborted, true);
});

test("shared panel retains error/retry and the legacy route reuses it without a nested main", async () => {
  let calls = 0;
  const view = panel(roles[3], async () => { if (++calls === 1) throw new Error("조회 실패"); return { count: 0, results: [] }; });
  view.render(); view.flush(); await tick();
  assert.equal(find(view.render(), node => node.props?.role === "alert").props.children, "조회 실패");
  find(view.render(), node => node.props?.children === "새로고침").props.onClick();
  view.render(); view.flush(); await tick();
  assert.match(text(view.render()), /해당 평가가 없습니다/);
  assert.equal(find(view.render(), node => node.props?.role === "alert"), undefined);
  assert.equal(view.render().type, "section");
  const { default: Legacy } = load("app/admin/feedback/page.tsx", { Link: noop, AdminFeedbackPanel: noop, styles });
  const tree = Legacy();
  assert.equal(tree.type, "main");
  assert.equal(find(tree, node => node.props?.href === "/admin").props.children, "← 관리자");
  assert.equal(tree.props.children[1].type, noop);
  view.unmount();
});
