import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(file, dependencies = {}) {
  const source = readFileSync(new URL("../" + file, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  const module = { exports: {} };
  new Function("module", "exports", "require", outputText)(module, module.exports, name => {
    if (!(name in dependencies)) throw new Error("Unexpected dependency: " + name);
    return dependencies[name];
  });
  return module.exports;
}
const client = load("lib/api/client.ts");
const routes = load("lib/team-community.ts");
const { safeMemberReturnPath } = load("lib/member-return-path.ts");

test("member return paths reject external destinations and normalize pagination", () => {
  for (const path of [null, "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/admin", "/community/members/0", "/community/members/9007199254740992"]) {
    assert.equal(safeMemberReturnPath(path), null, String(path));
  }
  assert.equal(safeMemberReturnPath("/community/members/21?tab=comments&page=2"), "/community/members/21?tab=comments&page=2");
  assert.equal(safeMemberReturnPath("/community/members/21?tab=invalid&page=-1"), "/community/members/21?tab=posts&page=1");
});

test("member activity links route to the original board and encode post IDs", () => {
  assert.equal(routes.getCommunityPostHref({ id: "free/a b", board: "free", teamCode: "" }), "/community?post=free%2Fa%20b");
  const url = new URL(routes.getCommunityPostHref({ id: "lg/a b", board: "teams", teamCode: "LG" }), "https://local.invalid");
  assert.equal(url.pathname, "/community/teams");
  assert.equal(url.searchParams.get("post"), "lg/a b");
  assert.equal(url.searchParams.get("team"), "LG");
});

function apiWith(handler) {
  return load("lib/community-member-api.ts", { "./api/client": client, "./member-auth-request": { memberFetch: handler }, "./team-community": routes });
}
const post = { id: "free-1", title: "제목", board: "free", teamCode: "", createdAt: "2026-09-28T01:00:00Z" };
const page = results => ({ count: results.length, next: null, previous: null, results });

test("member activity uses authenticated requests and preserves server order", async () => {
  const calls = [];
  const posts = [post, { ...post, id: "free-2", createdAt: "2026-09-27T01:00:00Z" }];
  const api = apiWith(async (url, init) => {
    calls.push(url);
    assert.equal(init.cache, "no-store");
    assert.ok(init.signal instanceof AbortSignal);
    return Response.json(url.endsWith("/21/") ? { id: 21, nickname: "회원" } : page(posts));
  });
  assert.equal((await api.fetchCommunityMember(21)).id, 21);
  assert.deepEqual((await api.fetchCommunityMemberPosts(21, 2)).results, posts);
  assert.deepEqual(calls, ["/api/v2/community/members/21/", "/api/v2/community/members/21/posts/?page=2&page_size=20"]);
});

test("member activity rejects mismatched identities and missing comment destinations", async () => {
  await assert.rejects(apiWith(async () => Response.json({ id: 22, nickname: "다른회원" })).fetchCommunityMember(21), error => error.status === 502);
  await assert.rejects(apiWith(async () => Response.json(page([{ id: 1, content: "댓글", createdAt: post.createdAt, postId: "x" }]))).fetchCommunityMemberComments(21), error => error.status === 502);
  for (const status of [401, 403, 404]) {
    await assert.rejects(apiWith(async () => Response.json({ detail: "거부" }, { status })).fetchCommunityMember(21), error => error.status === status);
  }
});

test("invalid IDs and aborted requests never invoke the member transport", async () => {
  let calls = 0;
  const api = apiWith(async () => { calls++; return Response.json(page([])); });
  await assert.rejects(api.fetchCommunityMember(0), error => error.status === 400);
  assert.throws(() => api.fetchCommunityMemberPosts(21, -1), error => error.status === 400);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(api.fetchCommunityMemberPosts(21, 1, controller.signal), error => error.name === "AbortError");
  assert.equal(calls, 0);
});
