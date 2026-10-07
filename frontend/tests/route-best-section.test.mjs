import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const React = require("react");
const source = readFileSync(new URL("../components/route-best-section.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
const flush = () => new Promise(resolve => setImmediate(resolve));
function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  return React.isValidElement(node) ? [node, ...nodes(node.props.children)] : [];
}
function harness() {
  const states = [], requests = [];
  let cursor = 0, previous, cleanup;
  const mocks = {
    react: { useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    }, useEffect(effect, deps) {
      if (previous && deps.every((value, i) => value === previous[i])) return;
      cleanup?.(); previous = deps; cleanup = effect();
    } },
    "@/lib/course-api": { fetchBestCourses(code, signal) {
      return new Promise((resolve, reject) => requests.push({ code, signal, resolve, reject }));
    } },
    "./route-card": { RouteCard: "card" }, "./route-skeleton": { RouteCardsSkeleton: "skeleton" },
    "./route-best-section.module.css": {},
  };
  const mod = { exports: {} };
  new Function("require", "exports", code)(id => id in mocks ? mocks[id] : require(id), mod.exports);
  return { requests, render(props) { cursor = 0; return mod.exports.RouteBestSection(props); }, unmount() { cleanup?.(); } };
}

test("best section changes stadium without displaying a late previous response", async () => {
  const h = harness(), routes = [];
  h.render({ stadium: "사직", routes });
  assert.equal(h.requests[0].code, "SAJIK");
  h.render({ stadium: "잠실", routes });
  assert.equal(h.requests[0].signal.aborted, true);
  h.requests[1].resolve([{ id: "jamsil", likes: 3 }]);
  await flush();
  h.requests[0].resolve([{ id: "sajik", likes: 10 }]);
  await flush();
  const cards = nodes(h.render({ stadium: "잠실", routes })).filter(node => node.type === "card");
  assert.deepEqual(cards.map(node => node.props.route.id), ["jamsil"]);
  h.unmount();
});

test("best section retries failures and handles empty results", async () => {
  const h = harness(), props = { stadium: "전체", routes: [] };
  h.render(props);
  assert.equal(h.requests[0].code, "");
  h.requests[0].reject(new Error("offline"));
  await flush();
  const failed = nodes(h.render(props));
  assert.ok(failed.some(node => node.props.role === "alert"));
  failed.find(node => node.type === "button").props.onClick();
  h.render(props);
  h.requests[1].resolve([]);
  await flush();
  assert.ok(nodes(h.render(props)).some(node => node.props.role === "status"));
  h.unmount();
});

test("published route updates refetch ranks while unchanged props do not", async () => {
  const h = harness(), props = { stadium: "사직", routes: [] };
  h.render(props); h.requests[0].resolve([{ id: "first", likes: 1 }]); await flush();
  h.render(props);
  assert.equal(h.requests.length, 1);
  const changed = { ...props, routes: [{ id: "other", likes: 5 }] };
  assert.ok(nodes(h.render(changed)).some(node => node.type === "skeleton"));
  h.requests[1].resolve([{ id: "other", likes: 5 }]); await flush();
  assert.equal(nodes(h.render(changed)).find(node => node.type === "card").props.route.id, "other");
  h.unmount();
});
