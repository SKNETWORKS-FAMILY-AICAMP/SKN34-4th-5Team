import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const React = require("react");
const writer = readFileSync(new URL("../components/route-writer.tsx", import.meta.url), "utf8");
const planner = readFileSync(new URL("../components/nearby-route-planner.tsx", import.meta.url), "utf8");
const guide = readFileSync(new URL("../components/route-guide.tsx", import.meta.url), "utf8");
const stadium = { code: "JAMSIL", name: "잠실", lat: 37.5, lng: 127 };
const stop = name => ({ name, category: "직접 지정", lat: 37.5, lng: 127, isMapPoint: true });
const scratch = mkdtempSync(join(tmpdir(), "kbo-writer-access-test-"));
after(() => rmSync(scratch, { recursive: true }));
for (const name of ["client-id", "route-draft", "stadiums", "stadium-locations", "google-lodging", "community-rich-content"]) {
  const source = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  writeFileSync(join(scratch, `${name}.js`), ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText);
}
const draftService = createRequire(join(scratch, "entry.cjs"))("./route-draft.js");
const originalData = { stadiumCode: "JAMSIL", title: "원래 초안", content: "복원할 이야기", duration: "반나절", tags: ["첫 직관"], stops: [stop("원래 장소")], start: { lat: 37.4, lng: 127.1 }, tab: "write", travelMode: "transit" };
const originalRaw = JSON.stringify({ version: 1, revision: "original", updatedAt: "2026-09-01T00:00:00.000Z", data: originalData });

// Execute the actual component and its handlers, with hook state and boundary services stubbed.
// No DOM/test dependency is needed; Kakao rendering is covered by the existing planner tests.
function harness(status = "anonymous", sample = false, userId = 1, draftContext = "new:JAMSIL") {
  let cursor = 0;
  let auth = { status, user: status === "authenticated" ? { id: userId } : null };
  const state = [], effects = [], listeners = new Map(), calls = [];
  const storage = new Map([[draftService.ROUTE_DRAFT_PREFIX + draftContext, originalRaw]]);
  const browserStorage = {
    getItem(key) { calls.push(["getItem", key]); return storage.get(key) ?? null; },
    setItem(key, value) { calls.push(["setItem", key]); storage.set(key, value); },
    removeItem(key) { calls.push(["removeItem", key]); storage.delete(key); },
  };
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === "function" ? initial() : initial; return [state[i], value => { state[i] = typeof value === "function" ? value(state[i]) : value; }]; },
    useRef(value) { const i = cursor++; return state[i] ??= { current: value }; },
    useCallback(fn) { cursor++; return fn; },
    useSyncExternalStore() { cursor++; return true; },
    useEffect(fn) { cursor++; effects.push(fn); },
    useLayoutEffect(fn) { cursor++; effects.push(fn); },
  };
  const chat = { onContextChange() {}, context: {}, onReset() {}, pending: "", registerCourseTarget() {}, takePendingCourse() {} };
  const drafts = {
    browserDraftStorage() { calls.push("storage"); return browserStorage; },
    readRouteDraft(storage, key) { calls.push(["read", key]); return draftService.readRouteDraft(storage, key); },
    recoverRouteDraft: draftService.recoverRouteDraft,
    saveRouteDraft(storage, key, data, expectedRaw) { calls.push(["write", key]); return draftService.saveRouteDraft(storage, key, data, expectedRaw); },
    removeRouteDraft(storage, key, expectedRaw) { calls.push(["delete", key]); return draftService.removeRouteDraft(storage, key, expectedRaw); },
    createDraftAutosave(flush) { calls.push("autosave"); return { changed() {}, flush, stop(first) { if (first) flush(); } }; },
  };
  const win = {
    location: { href: "http://localhost/routes/new", origin: "http://localhost", pathname: "/routes/new", search: "" },
    addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener() {},
    setTimeout() { return 1; },
    history: { state: null, replaceState(_state, _unused, url) { win.location.href = String(url); } },
  };
  const doc = { addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener() {}, activeElement: null };
  const routeService = { useRoutes: () => [], useRoutesReady: () => true, useRoutesError: () => "", saveRoute: async () => { calls.push("server-save"); return { id: "saved" }; } };
  const sandbox = { exports: {}, window: win, document: doc, URL, clearTimeout() {}, requestAnimationFrame(fn) { fn(); }, console,
    require(id) {
      if (id === "react") return hooks;
      if (id === "react/jsx-runtime") return require(id);
      if (id === "next/navigation") return { useRouter: () => ({ push(url) { calls.push(["navigate", url]); } }) };
      if (id === "next/link") return { default: "a", __esModule: true };
      if (id.endsWith("/member-auth")) return { useMemberAuth: () => auth };
      if (id.endsWith("/route-draft")) return drafts;
      if (id.endsWith("/routes")) return routeService;
      if (id.endsWith("/chat-provider")) return { useChat: () => chat, ChatSampleProvider: "sample-provider" };
      if (id.endsWith("/community-rich-content")) return { plainRichDoc: () => ({ blocks: [] }), richText: () => "" };
      if (id.endsWith("/route-content")) return { routeContentToText: value => value };
      if (id.endsWith("/team-community")) return { teamBoards: [] };
      if (id.endsWith("/drawn-course")) return { withCourseStart: value => value };
      if (id.endsWith("/nearby-places")) return { MAX_ROUTE_STOPS: 12 };
      if (id.endsWith("/baseball/client")) return { fetchBaseballStadiums: async () => { calls.push("stadiums"); return { results: [stadium] }; } };
      if (id.endsWith("/baseball/adapters")) return { adaptStadium: value => value };
      return new Proxy({}, { get: (_, name) => name });
    }, AbortController,
  };
  vm.runInNewContext(ts.transpileModule(writer + "\nexports.WriterForm = WriterForm;", { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText, sandbox);
  const render = (component = sandbox.exports.WriterForm, props = { stadiums: [stadium], initial: stadium, sample }) => { cursor = 0; effects.length = 0; return component(props); };
  return { render, wrapper: sandbox.exports.default, calls, storage, listeners, setAuth(value) { auth = value; }, mountEffects() { return effects.map(fn => fn()).filter(fn => typeof fn === "function"); } };
}
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!React.isValidElement(node)) return [];
  return [node, ...elements(node.props.children)];
}
const find = (tree, predicate) => elements(tree).find(predicate);
const plannerProps = tree => find(tree, item => item.type === "NearbyRoutePlanner").props;

test("anonymous new writer loads public stadiums, but editing remains login-only", async () => {
  const h = harness();
  h.render(h.wrapper, {}); h.mountEffects();
  await Promise.resolve();
  const fresh = h.render(h.wrapper, {});
  assert.ok(h.calls.includes("stadiums"));
  assert.equal(typeof fresh.type, "function");
  const edit = h.render(h.wrapper, { editId: "private" });
  assert.equal(edit.type, "main");
  assert.ok(find(edit, item => item.props.href?.startsWith("/login?next=")));
});

test("guest course mutations stay in memory, cannot save, and protect unsaved navigation", async () => {
  const h = harness();
  let tree = h.render(); h.mountEffects();
  assert.equal(find(tree, item => item.props.id === "route-title"), undefined);
  assert.equal(find(tree, item => item.type === "CommunityRichEditor"), undefined);
  assert.equal(find(tree, item => item.props.className === "writer-save-area"), undefined);
  assert.equal(plannerProps(tree).allowSave, false);
  assert.equal(h.calls.length, 0);
  plannerProps(tree).onChange([stop("A"), stop("B")]);
  tree = h.render();
  assert.deepEqual(Array.from(plannerProps(tree).stops, item => item.name), ["A", "B"]);
  plannerProps(tree).onChange([stop("B"), stop("A")]);
  tree = h.render();
  assert.deepEqual(Array.from(plannerProps(tree).stops, item => item.name), ["B", "A"]);
  await plannerProps(tree).onSaveCourse();
  find(tree, item => item.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(h.calls.length, 0);
  let prevented = false;
  h.listeners.get("beforeunload")({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  h.listeners.get("click")({ button: 0, target: { closest: () => ({ href: "http://localhost/routes", hasAttribute: () => false }) }, preventDefault() {}, stopPropagation() {} });
  tree = h.render();
  find(tree, item => item.type === "button" && item.props.className === "button button-primary" && item.props.children === "떠나기").props.onClick();
  assert.deepEqual(h.calls, [["navigate", "/routes"]]);
  assert.deepEqual([...h.storage], [[draftService.ROUTE_DRAFT_PREFIX + "new:JAMSIL", originalRaw]]);
});

test("members restore valid original new/edit/copy drafts and retain canonical autosave keys", async () => {
  assert.ok(draftService.parseRouteDraft(originalRaw));
  for (const context of ["new:JAMSIL", "edit:42", "copy:42"]) {
    const h = harness("authenticated", false, 1, context);
    const props = { stadiums: [stadium], initial: stadium, ...(context.startsWith("new:") ? {} : { existing: { id: "42", title: "저장된 코스", content: "", stadium: stadium.name, stops: [], owned: true }, copying: context.startsWith("copy:") }) };
    const tree = h.render(undefined, props); h.mountEffects();
    assert.equal(plannerProps(tree).allowSave, true);
    assert.ok(find(tree, item => item.type === "CommunityRichEditor"));
    assert.equal(find(tree, item => item.props.id === "route-title").props.value, originalData.title);
    assert.deepEqual(plannerProps(tree).stops, originalData.stops);
    assert.deepEqual(plannerProps(tree).initialStart, originalData.start);
    assert.equal(plannerProps(tree).travelMode, originalData.travelMode);
    assert.ok(h.calls.includes("autosave"));
    assert.ok(h.calls.some(call => call[0] === "read" && call[1] === context));
    plannerProps(tree).onChange([stop("new")]);
    const changed = h.render(undefined, props); h.mountEffects();
    find(changed, item => item.type === "button" && item.props.children === "임시저장").props.onClick();
    assert.equal(draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + context)).data.stops[0].name, "new");
    await plannerProps(changed).onSaveCourse();
    assert.equal(h.storage.has(draftService.ROUTE_DRAFT_PREFIX + context), false);
    const saved = h.render(undefined, props);
    plannerProps(saved).onChange([stop("after save")]);
    const updated = h.render(undefined, props); h.mountEffects();
    find(updated, item => item.type === "button" && item.props.children === "임시저장").props.onClick();
    assert.equal(draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + "edit:saved")).data.stops[0].name, "after save");
  }
});

test("account changes and logout remount the writer without changing baseline shared draft keys", async () => {
  const h = harness("authenticated");
  h.render(h.wrapper, {}); h.mountEffects();
  await Promise.resolve();
  const member = h.render(h.wrapper, {});
  h.setAuth({ status: "authenticated", user: { id: 2 } });
  const otherMember = h.render(h.wrapper, {});
  h.setAuth({ status: "anonymous", user: null });
  const guest = h.render(h.wrapper, {});
  assert.notEqual(member.key, otherMember.key);
  assert.notEqual(otherMember.key, guest.key);
});

test("authenticated guide samples never restore drafts, upload images, or server-save", async () => {
  const h = harness("authenticated", true);
  const tree = h.render(); h.mountEffects();
  assert.equal(find(tree, item => item.type === "CommunityRichEditor").props.imageUploadDisabled, true);
  plannerProps(tree).onChange([stop("sample")]);
  await plannerProps(tree).onSaveCourse();
  find(tree, item => item.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(h.calls.length, 0);
  assert.equal(h.listeners.has("beforeunload"), false);
});

test("identity changes remount the form, and guest completion/guide skip unavailable saves", () => {
  assert.match(writer, /key=\{`\$\{authStatus\}:\$\{user\?\.id \?\? "guest"\}/);
  assert.match(writer, /if \(!activeForm.current\) return;\s*const persisted = await saveRoute\(route\);\s*if \(!activeForm.current\) return;/);
  assert.match(planner, /const canSaveCourse = allowSave && canComplete/);
  assert.match(planner, /originReplacement=\{courseCompleted \? allowSave \?/);
  const selectSteps = guide.slice(guide.indexOf("const selectSteps ="), guide.indexOf("const tour = driver"));
  const js = ts.transpileModule(selectSteps + "\nexports.selectSteps = selectSteps;", {}).outputText;
  for (const includeMemberSteps of [false, true]) {
    const context = { exports: {}, includeMemberSteps };
    vm.runInNewContext(js, context);
    const steps = ["내 코스", "코스 저장", "코스 저장 완료", "가이드를 마쳤어요"].map(title => ({ popover: { title } }));
    assert.equal(context.exports.selectSteps(steps).length, includeMemberSteps ? 4 : 2);
  }
});
